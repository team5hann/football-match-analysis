import gc
import math
import subprocess
import tempfile
import time
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
from app.services.ball_detection import detect_ball_candidates
from app.services.ball_tracking import clean_ball_trajectory

settings = get_settings()
PERSON_CLASS_ID = 0
BALL_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "football-ball-detection.pt"
BALL_CLASS_ID = 0

# Player track_id now comes straight from Ultralytics' built-in ByteTrack
# (model.track(..., tracker=TRACKER_CONFIG)) instead of the old per-frame IoU
# matching in analysis.py. ByteTrack keeps a Kalman motion model per track and
# recovers it across short detection gaps, so one real player stays one track_id
# far more often - which in turn feeds cleaner identity merging downstream.
# The config is a football-tuned copy of Ultralytics' bytetrack.yaml (longer
# track buffer for occlusions, stricter new-track threshold); see the file.
TRACKER_CONFIG = str(Path(__file__).resolve().parent / "football_bytetrack.yaml")

# Number of frames sent to YOLO in a single inference call. Batching keeps the
# GPU busy between frames and is the main speed-up over one-frame-at-a-time.
# The dev GPU (T500) has only 4 GB VRAM and one 1080p frame already needs
# ~1 GB, so 8 is a deliberately conservative starting point. If a batch raises
# a CUDA out-of-memory error the code below automatically halves the batch size
# (8 -> 4 -> 2 -> 1) and retries, so lower this only to skip the first failed
# attempts, or raise it when running on a GPU with more headroom.
DETECTION_BATCH_SIZE = 8

# Detections are streamed to the database in chunks instead of being held in the
# ORM session until one final commit. A 110s clip sampled at ~30fps is ~3300
# frames * ~13 boxes ~= 43k rows; keeping all of those as pending ORM instances
# (plus the identity map and the single giant flush) is what pushed a longer
# video into a host-RAM OOM. Flushing every few thousand rows caps the session's
# footprint to roughly this many Detection mappings at a time.
DETECTION_FLUSH_ROWS = 3000


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


def _release_predictor_gpu(model, device: str) -> None:
    """Drop a YOLO model's inference predictor and free its CUDA allocations.

    ``model.track(stream=True)`` builds a persistent ``model.predictor`` that
    holds the tracking model's weights + activation buffers on the GPU for the
    whole run. On a 4 GB card that is almost the entire device, so the *next*
    model to run (the separate ball model) has no room to allocate and the
    driver call wedges instead of raising a clean CUDA OOM. Tearing the
    predictor down between phases returns that memory.
    """
    try:
        predictor = getattr(model, "predictor", None)
        if predictor is not None:
            for attr in ("model", "trackers"):
                if hasattr(predictor, attr):
                    setattr(predictor, attr, None)
        model.predictor = None
    except Exception:  # best effort - the empty_cache below still helps
        pass
    try:
        import gc

        gc.collect()
    except Exception:
        pass
    _empty_cuda_cache(device)


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

    file_path = video.file_path
    video_width = video.width

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

            model = YOLO(settings.player_model)
            ball_model = YOLO(str(BALL_MODEL_PATH))
            model.to(device)
            ball_model.to(device)

        with tempfile.TemporaryDirectory(prefix="football-frames-") as temp_dir:
            sample_interval = settings.detection_sample_interval_seconds
            frames = extract_frames(file_path, Path(temp_dir), sample_interval)
            frame_paths = [str(path) for path in frames]

            # Detections are accumulated here and flushed to the DB in bounded
            # chunks (see DETECTION_FLUSH_ROWS) so a long video never holds the
            # whole result set in memory at once.
            pending: list[dict] = []

            def flush(force: bool = False) -> None:
                if pending and (force or len(pending) >= DETECTION_FLUSH_ROWS):
                    db.bulk_insert_mappings(Detection, pending)
                    db.commit()
                    pending.clear()
                    # YOLO Results hold the full frame image and sit in reference
                    # cycles, so they are only reclaimed by the cyclic GC; force
                    # it here so they cannot pile up across a long video.
                    gc.collect()

            # --- players: ByteTrack over the ordered sampled frames --------------
            # Tracking is inherently sequential, so we lose the GPU batching the
            # ball model still uses; the continuity win is worth it.
            frame_total = len(frame_paths)
            print(
                f"[detection] player tracking with {TRACKER_CONFIG} over {frame_total} frames",
                flush=True,
            )
            # Pass the frames *directory*, never a Python list: Ultralytics treats
            # a list as a batch of in-memory images and eagerly decodes every one
            # (3300 * 1080p ~= 20 GB -> host OOM), whereas a directory streams one
            # frame at a time. persist=True keeps the ByteTrack state across the
            # whole sequence - with the default persist=False Ultralytics sees a
            # new filename each frame, assumes a new video, and resets the tracker
            # every frame, so track_ids would never carry across frames.
            player_stream = model.track(
                source=temp_dir,
                stream=True,
                persist=True,
                tracker=TRACKER_CONFIG,
                classes=[PERSON_CLASS_ID],
                imgsz=settings.player_imgsz,
                device=device,
                half=player_half,
                verbose=False,
            )
            track_started = time.perf_counter()
            for frame_number, result in enumerate(player_stream):
                pending.extend(
                    _result_rows(video_id, frame_number, sample_interval, result, PERSON_CLASS_ID, "player")
                )
                # Drop the yielded Results object (it still holds the full frame
                # image) before pulling the next one.
                del result
                flush()
                # Tracking is sequential and can run for minutes on a modest GPU;
                # log periodic progress so a long run is observable.
                if (frame_number + 1) % 100 == 0 or frame_number + 1 == frame_total:
                    elapsed = time.perf_counter() - track_started
                    rate = (frame_number + 1) / elapsed if elapsed else 0.0
                    print(
                        f"[detection] tracked {frame_number + 1}/{frame_total} frames "
                        f"({elapsed:.0f}s, {rate:.1f} fps)",
                        flush=True,
                    )
                    gc.collect()
            flush(force=True)

            # ByteTrack's persistent predictor still owns most of the GPU here;
            # hand that memory back before the separate ball model runs.
            del player_stream
            _release_predictor_gpu(model, device)

            # --- ball: full frame + tiles, then trajectory cleaning -----------
            # Candidates are tiny (a few per frame), so the whole match fits in
            # memory; the trajectory filter needs them all to pick one ball per
            # frame and interpolate short gaps.
            if ball_model is not None:

                def infer(images: list) -> list:
                    return ball_model(
                        images,
                        device=device,
                        half=ball_half,
                        imgsz=settings.ball_imgsz,
                        conf=settings.ball_confidence,
                        verbose=False,
                    )

                def on_oom(exc: Exception, batch_size: int) -> int | None:
                    if not _is_cuda_oom(exc) or batch_size <= 1:
                        return None
                    _empty_cuda_cache(device)
                    smaller = max(1, batch_size // 2)
                    print(f"[detection] CUDA out of memory, retrying with batch size {smaller}", flush=True)
                    return smaller

                candidates = []
                for frame_candidates in detect_ball_candidates(
                    infer,
                    frame_paths,
                    class_id=BALL_CLASS_ID,
                    tile_grid=settings.ball_tile_grid,
                    batch_size=DETECTION_BATCH_SIZE,
                    on_oom=on_oom,
                ):
                    candidates.extend(frame_candidates)
                trajectory = clean_ball_trajectory(
                    candidates,
                    frame_width=video_width or _frame_width(frame_paths),
                    max_gap_frames=int(settings.ball_max_gap_seconds / sample_interval),
                )
                print(
                    f"[detection] ball: {len(candidates)} candidates -> "
                    f"{sum(not p.interpolated for p in trajectory)} on trajectory, "
                    f"{sum(p.interpolated for p in trajectory)} interpolated, "
                    f"{len(frame_paths)} frames",
                    flush=True,
                )
                for start in range(0, len(trajectory), DETECTION_FLUSH_ROWS):
                    pending.extend(
                        _ball_rows(video_id, sample_interval, trajectory[start : start + DETECTION_FLUSH_ROWS])
                    )
                    flush()
                flush(force=True)

        video = db.get(Video, video_id)
        video.status = VideoStatus.ANALYZED
        video.match.status = MatchStatus.ANALYZED
        db.commit()
    except Exception as exc:
        db.rollback()
        video = db.get(Video, video_id)
        if video is not None:
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


def _result_rows(
    video_id: int,
    frame_number: int,
    sample_interval: float,
    result,
    target_class_id: int,
    class_name: str,
) -> list[dict]:
    """Turn one frame's boxes into ``Detection`` mapping dicts for a bulk insert.

    Plain dicts (not ORM instances) keep the streaming insert path cheap: they
    never enter the session's identity map, so memory stays flat over a long
    video. ``result.boxes.id`` (present in track mode) becomes ``track_id``.
    """
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    xyxy = boxes.xyxy.tolist()
    conf = boxes.conf.tolist()
    cls = boxes.cls.tolist()
    ids = boxes.id.tolist() if getattr(boxes, "id", None) is not None else [None] * len(xyxy)
    frame_timestamp = round(frame_number * sample_interval, 6)
    rows: list[dict] = []
    for box, confidence, class_id, track_id in zip(xyxy, conf, cls, ids):
        if int(class_id) != target_class_id:
            continue
        x_min, y_min, x_max, y_max = box
        # FP16 inference can occasionally emit non-finite coordinates or scores;
        # such boxes are meaningless and would break the JSON / float columns.
        if not all(math.isfinite(value) for value in (x_min, y_min, x_max, y_max, confidence)):
            continue
        rows.append(
            {
                "video_id": video_id,
                "frame_timestamp": frame_timestamp,
                "bounding_box": {
                    "x": round(x_min, 3),
                    "y": round(y_min, 3),
                    "width": round(x_max - x_min, 3),
                    "height": round(y_max - y_min, 3),
                },
                "class_name": class_name,
                "confidence": round(float(confidence), 4),
                "track_id": int(track_id) if track_id is not None and math.isfinite(track_id) else None,
            }
        )
    return rows


def _frame_width(frame_paths: list[str]) -> int:
    import cv2

    image = cv2.imread(frame_paths[0]) if frame_paths else None
    return image.shape[1] if image is not None else 1920


def _ball_rows(video_id: int, sample_interval: float, trajectory) -> list[dict]:
    return [
        {
            "video_id": video_id,
            "frame_timestamp": round(point.frame * sample_interval, 6),
            "bounding_box": {
                "x": round(point.x1, 3),
                "y": round(point.y1, 3),
                "width": round(point.x2 - point.x1, 3),
                "height": round(point.y2 - point.y1, 3),
            },
            "class_name": "ball",
            "confidence": round(point.confidence, 4),
            "track_id": None,
            "is_interpolated": point.interpolated,
        }
        for point in trajectory
        if all(math.isfinite(value) for value in (point.x1, point.y1, point.x2, point.y2, point.confidence))
    ]


def list_detections(video_id: int, db: Session) -> list[Detection]:
    return db.scalars(
        select(Detection).where(Detection.video_id == video_id).order_by(Detection.frame_timestamp, Detection.id)
    ).all()