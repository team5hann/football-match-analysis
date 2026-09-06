import math
import subprocess
import tempfile
from functools import lru_cache
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
from app.models.homography import FrameHomography
from app.models.player_identity import MatchPlayer, TrackPlayerLink
from app.models.player_stats import MatchPlayerStats
from app.models.video import Video

settings = get_settings()
PERSON_CLASS_ID = 0
BALL_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "football-ball-detection.pt"
BALL_CLASS_ID = 0

# Number of frames sent to YOLO in a single inference call. Batching keeps the
# GPU busy between frames and is the main speed-up over one-frame-at-a-time.
# The dev GPU (T500) has only 4 GB VRAM and one 1080p frame already needs
# ~1 GB, so 8 is a deliberately conservative starting point. If a batch raises
# a CUDA out-of-memory error the code below automatically halves the batch size
# (8 -> 4 -> 2 -> 1) and retries, so lower this only to skip the first failed
# attempts, or raise it when running on a GPU with more headroom.
DETECTION_BATCH_SIZE = 8


@lru_cache(maxsize=1)
def resolve_inference_device() -> str:
    """Return the device string YOLO inference should run on.

    Uses CUDA when a GPU is visible to PyTorch, otherwise falls back to CPU
    without raising. The result is logged once so the effective device can be
    confirmed from the container logs.
    """
    device = "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            device = "cuda"
            gpu_name = torch.cuda.get_device_name(0)
            print(f"[detection] Running inference on: {device} ({gpu_name})", flush=True)
        else:
            print("[detection] Running inference on: cpu (CUDA not available)", flush=True)
    except Exception as exc:  # torch missing or CUDA probe failed
        print(f"[detection] Running inference on: cpu (device probe failed: {exc})", flush=True)
    return device


def _is_cuda_oom(exc: Exception) -> bool:
    return isinstance(exc, RuntimeError) and "out of memory" in str(exc).lower()


def _empty_cuda_cache(device: str) -> None:
    if device != "cuda":
        return
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:  # nothing we can do, the retry will still be attempted
        pass


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

        device = resolve_inference_device()
        # FP16 roughly halves memory traffic and speeds up inference on the GPU;
        # it is only well supported on CUDA, so keep FP32 on the CPU fallback.
        # The general yolov8n model is FP16-stable, but the specialised ball model
        # regresses NaN box coordinates in half precision (every ball box comes
        # back non-finite), so it always runs in FP32 to preserve ball detection.
        player_half = device == "cuda"
        ball_half = False
        print(
            f"[detection] batch size: {DETECTION_BATCH_SIZE}, "
            f"half precision: player={player_half} ball={ball_half}",
            flush=True,
        )

        if model is None:
            from ultralytics import YOLO

            model = YOLO("yolov8n.pt")
            ball_model = YOLO(str(BALL_MODEL_PATH))
            model.to(device)
            ball_model.to(device)

        with tempfile.TemporaryDirectory(prefix="football-frames-") as temp_dir:
            sample_interval = settings.detection_sample_interval_seconds
            frames = extract_frames(video.file_path, Path(temp_dir), sample_interval)

            batch_size = DETECTION_BATCH_SIZE
            base = 0
            while base < len(frames):
                batch_paths = [str(path) for path in frames[base : base + batch_size]]
                try:
                    player_results = model(batch_paths, device=device, half=player_half, verbose=False)
                    ball_results = (
                        ball_model(batch_paths, device=device, half=ball_half, verbose=False)
                        if ball_model is not None
                        else None
                    )
                except Exception as exc:  # noqa: BLE001 - narrow to CUDA OOM below
                    if _is_cuda_oom(exc) and batch_size > 1:
                        batch_size = max(1, batch_size // 2)
                        _empty_cuda_cache(device)
                        print(f"[detection] CUDA out of memory, retrying with batch size {batch_size}", flush=True)
                        continue
                    raise

                # Ultralytics preserves input order, so result i belongs to
                # frame `base + i`; keep the original per-frame timestamp mapping.
                for offset, result in enumerate(player_results):
                    _store_detections(
                        db, video_id, base + offset, sample_interval, [result], PERSON_CLASS_ID, "player"
                    )
                if ball_results is not None:
                    for offset, result in enumerate(ball_results):
                        _store_detections(
                            db, video_id, base + offset, sample_interval, [result], BALL_CLASS_ID, "ball"
                        )
                base += len(batch_paths)

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
    db.execute(delete(FrameHomography).where(FrameHomography.video_id == video_id))
    db.execute(delete(PlayerMetric).where(PlayerMetric.match_id == video.match_id))
    db.execute(delete(MatchPlayerStats).where(MatchPlayerStats.match_id == video.match_id))
    db.execute(delete(TrackPlayerLink).where(TrackPlayerLink.match_id == video.match_id))
    db.execute(delete(MatchPlayer).where(MatchPlayer.match_id == video.match_id))
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
            # FP16 inference can occasionally emit non-finite coordinates or
            # scores; such boxes are meaningless and would break the JSON /
            # float columns, so drop them instead of failing the whole run.
            if not all(math.isfinite(value) for value in (x_min, y_min, x_max, y_max, confidence)):
                continue
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