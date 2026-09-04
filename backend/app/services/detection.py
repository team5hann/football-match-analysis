import subprocess
import tempfile
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.detection import Detection
from app.models.enums import MatchStatus, VideoStatus
from app.models.video import Video

settings = get_settings()
SAMPLE_INTERVAL_SECONDS = 1
PERSON_CLASS_ID = 0
SPORTS_BALL_CLASS_ID = 32


def extract_frames(video_path: str, output_dir: Path, interval_seconds: int = SAMPLE_INTERVAL_SECONDS) -> list[Path]:
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
            f"fps=1/{interval_seconds}",
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


def run_detection(video_id: int, model=None, session_factory=SessionLocal) -> None:
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

        with tempfile.TemporaryDirectory(prefix="football-frames-") as temp_dir:
            frames = extract_frames(video.file_path, Path(temp_dir))
            for frame_number, frame_path in enumerate(frames):
                results = model(str(frame_path), verbose=False)
                for result in results:
                    if result.boxes is None:
                        continue
                    for box, confidence, class_id in zip(
                        result.boxes.xyxy.tolist(),
                        result.boxes.conf.tolist(),
                        result.boxes.cls.tolist(),
                    ):
                        class_id = int(class_id)
                        if class_id == PERSON_CLASS_ID:
                            class_name = "player"
                        elif class_id == SPORTS_BALL_CLASS_ID:
                            class_name = "ball"
                        else:
                            # TODO: fine-tune a football-specific model for reliable ball detection.
                            continue
                        x_min, y_min, x_max, y_max = box
                        db.add(
                            Detection(
                                video_id=video_id,
                                frame_timestamp=frame_number * SAMPLE_INTERVAL_SECONDS,
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


def list_detections(video_id: int, db: Session) -> list[Detection]:
    return db.scalars(
        select(Detection).where(Detection.video_id == video_id).order_by(Detection.frame_timestamp, Detection.id)
    ).all()