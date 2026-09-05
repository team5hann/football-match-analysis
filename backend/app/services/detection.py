import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.analysis import MatchAnalysisSummary, PlayerMetric
from app.models.clip import Clip
from app.models.detection import Detection
from app.models.enums import MatchStatus, VideoStatus
from app.models.event import Event
from app.models.video import Video

settings = get_settings()
PERSON_CLASS_ID = 0
BALL_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "football-ball-detection.pt"
BALL_CLASS_ID = 0


def extract_frames(video_path: str, output_dir: Path, interval_seconds: float | None = None) -> list[Path]:
    if interval_seconds is None:
        interval_seconds = settings.detection_sample_interval_seconds
    sample_rate = 1 / interval_seconds
    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            settings.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-vf",
            f"fps={sample_rate:g}",
            "-q:v",
            "3",
            str(output_dir / "%06d.jpg"),
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
    return sorted(output_dir.glob("*.jpg"))


def run_detection(video_id: int, model=None, ball_model=None, session_factory=SessionLocal) -> None:
    db: Session = session_factory()
    video = db.get(Video, video_id)
    if video is None:
        db.close()
        return

    try:
        video.status = VideoStatus.PROCESSING
        video.match.status = MatchStatus.PROCESSING
        db.execute(delete(Detection).where(Detection.video_id == video_id))
        db.commit()

        if model is None:
            from ultralytics import YOLO

            model = YOLO("yolov8n.pt")
            ball_model = YOLO(str(BALL_MODEL_PATH))

        with tempfile.TemporaryDirectory(prefix="football-frames-") as temp_dir:
            sample_interval = settings.detection_sample_interval_seconds
            frames = extract_frames(video.file_path, Path(temp_dir), sample_interval)
            for frame_number, frame_path in enumerate(frames):
                player_results = model(str(frame_path), verbose=False)
                _store_detections(
                    db, video_id, frame_number, sample_interval, player_results, PERSON_CLASS_ID, "player"
                )
                if ball_model is not None:
                    ball_results = ball_model(str(frame_path), verbose=False)
                    _store_detections(db, video_id, frame_number, sample_interval, ball_results, BALL_CLASS_ID, "ball")
            video.status = VideoStatus.ANALYZED
            video.match.status = MatchStatus.ANALYZED
            db.commit()
    except Exception as exc:
        db.rollback()
        video.status = VideoStatus.FAILED
        video.error_message = f"Detection failed: {exc}"
        video.match.status = MatchStatus.FAILED
        db.commit()
    finally:
        db.close()


def reset_detection_data(video_id: int, db: Session) -> Video:
    video = db.get(Video, video_id)
    if video is None:
        raise ValueError("Video not found")

    # Analysis and clips are match-level records, so invalidate them for the whole match.
    db.execute(delete(Clip).where(Clip.match_id == video.match_id))
    db.execute(delete(Event).where(Event.video_id == video_id))
    db.execute(delete(PlayerMetric).where(PlayerMetric.match_id == video.match_id))
    db.execute(delete(MatchAnalysisSummary).where(MatchAnalysisSummary.match_id == video.match_id))
    db.execute(delete(Detection).where(Detection.video_id == video_id))
    video.status = VideoStatus.METADATA_EXTRACTED
    video.error_message = None
    video.match.status = MatchStatus.UPLOADED
    db.commit()
    return video


def _store_detections(
    db: Session,
    video_id: int,
    frame_number: int,
    sample_interval: float,
    results,
    target_class_id: int,
    class_name: str,
) -> None:
    for result in results:
        if result.boxes is None:
            continue
        for box, confidence, class_id in zip(
            result.boxes.xyxy.tolist(),
            result.boxes.conf.tolist(),
            result.boxes.cls.tolist(),
        ):
            if int(class_id) != target_class_id:
                continue
            x_min, y_min, x_max, y_max = box
            db.add(
                Detection(
                    video_id=video_id,
                    frame_timestamp=round(frame_number * sample_interval, 6),
                    bounding_box={
                        "x": round(x_min, 3),
                        "y": round(y_min, 3),
                        "width": round(x_max - x_min, 3),
                        "height": round(y_max - y_min, 3),
                    },
                    class_name=class_name,
                    confidence=round(float(confidence), 4),
                )
            )


def list_detections(video_id: int, db: Session) -> list[Detection]:
    return db.scalars(
        select(Detection).where(Detection.video_id == video_id).order_by(Detection.frame_timestamp, Detection.id)
    ).all()