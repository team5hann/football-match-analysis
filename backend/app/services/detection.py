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

settings = get_settings()
MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

# --- player model -------------------------------------------------------------
# Preferred: Roboflow's football-specific YOLOv8 player model (same source as the
# ball model, roboflow/sports examples/soccer/setup.sh; AGPL-3.0). It knows the
# difference between outfield players, goalkeepers and referees and was trained
# on wide pitch footage, so it finds far more of the small, distant players than
# the generic COCO model. Its classes are 0=ball, 1=goalkeeper, 2=player,
# 3=referee; the ball class is ignored here because the dedicated ball model
# below is better at it.
PLAYER_MODEL_PATH = MODELS_DIR / "football-player-detection.pt"
FOOTBALL_PLAYER_CLASS_MAP = {1: "player", 2: "player", 3: "referee"}
# The upstream pipeline runs this model at 1280 px: players on a wide shot are
# only a few dozen pixels tall and disappear at the default 640.
FOOTBALL_PLAYER_IMGSZ = 1280
# Fallback when the football weights are not present (e.g. a local run without
# the Docker build step): generic COCO yolov8n, class 0 = person.
PERSON_CLASS_ID = 0
GENERIC_PLAYER_CLASS_MAP = {PERSON_CLASS_ID: "player"}

# --- ball model ---------------------------------------------------------------
BALL_MODEL_PATH = MODELS_DIR / "football-ball-detection.pt"
BALL_CLASS_ID = 0
# The ball model is trained on 640x640 crops. Running it on a whole 1080p frame
# shrinks the ball to ~3 px and it is mostly missed, so - like the upstream
# pipeline (supervision InferenceSlicer) - the frame is cut into overlapping
# 640 px tiles and each tile is inferred at native resolution.
BALL_TILE_SIZE = 640
BALL_TILE_OVERLAP = 0.2
# Only one real ball exists. Among the candidates in a frame, one within this
# many pixels of the last accepted ball wins (highest confidence among those);
# otherwise the most confident candidate overall. This stops a single bright
# false positive (a sock, a cone, a spectator's head) from teleporting the ball,
# which used to break possession and therefore every pass/shot downstream.
BALL_MAX_JUMP_PX = 250.0

# Player track_id now comes straight from Ultralytics' built-in ByteTrack
# (model.track(..., tracker=TRACKER_CONFIG)) instead of the old per-frame IoU
# matching in analysis.py. ByteTrack keeps a Kalman motion model per track and
# recovers it across short detection gaps, so one real player stays one track_id
# far more often - which in turn feeds cleaner identity merging downstream.
TRACKER_CONFIG = "bytetrack.yaml"

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


def effective_sample_interval(configured_interval: float, video_fps: float | None) -> float:
    """Never sample faster than the video actually has frames.

    Asking ffmpeg for 60 fps from a 25/30 fps file just duplicates frames: twice
    the GPU time for zero new information. Cap the sampling rate at the source
    frame rate when ffprobe gave us one.
    """
    if video_fps and video_fps > 0:
        return max(configured_interval, 1.0 / video_fps)
    return configured_interval


def _tile_origins(length: int, tile: int, overlap: float) -> list[int]:
    """Start offsets of overlapping tiles covering ``length`` pixels."""
    if length <= tile:
        return [0]
    stride = max(1, int(tile * (1 - overlap)))
    origins = list(range(0, length - tile, stride))
    origins.append(length - tile)  # last tile flush with the edge
    return origins


def ball_tiles(image_width: int, image_height: int) -> list[tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) tiles covering the frame for sliced ball inference."""
    tile_w = min(BALL_TILE_SIZE, image_width)
    tile_h = min(BALL_TILE_SIZE, image_height)
    return [
        (x0, y0, x0 + tile_w, y0 + tile_h)
        for y0 in _tile_origins(image_height, tile_h, BALL_TILE_OVERLAP)
        for x0 in _tile_origins(image_width, tile_w, BALL_TILE_OVERLAP)
    ]


def select_ball(
    candidates: list[tuple[list[float], float]],
    previous_center: tuple[float, float] | None,
    max_jump_px: float = BALL_MAX_JUMP_PX,
) -> tuple[list[float], float] | None:
    """Pick the single most plausible ball from ``(xyxy, confidence)`` candidates."""
    finite = [
        (box, conf)
        for box, conf in candidates
        if all(math.isfinite(value) for value in (*box, conf))
    ]
    if not finite:
        return None
    if previous_center is not None:
        px, py = previous_center
        near = [
            (box, conf)
            for box, conf in finite
            if math.hypot((box[0] + box[2]) / 2 - px, (box[1] + box[3]) / 2 - py) <= max_jump_px
        ]
        if near:
            return max(near, key=lambda item: item[1])
    return max(finite, key=lambda item: item[1])


def _ball_candidates(results, tiles: list[tuple[int, int, int, int]]) -> list[tuple[list[float], float]]:
    """Shift per-tile ball boxes back into full-frame pixel coordinates."""
    candidates: list[tuple[list[float], float]] = []
    for (x0, y0, _x1, _y1), result in zip(tiles, results):
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            continue
        for box, conf, cls in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist()):
            if int(cls) != BALL_CLASS_ID:
                continue
            candidates.append(([box[0] + x0, box[1] + y0, box[2] + x0, box[3] + y0], float(conf)))
    return candidates


def _ball_row(video_id: int, frame_number: int, sample_interval: float, box: list[float], conf: float) -> dict:
    x_min, y_min, x_max, y_max = box
    return {
        "video_id": video_id,
        "frame_timestamp": round(frame_number * sample_interval, 6),
        "bounding_box": {
            "x": round(x_min, 3),
            "y": round(y_min, 3),
            "width": round(x_max - x_min, 3),
            "height": round(y_max - y_min, 3),
        },
        "class_name": "ball",
        "confidence": round(conf, 4),
        "track_id": None,
    }


def run_detection(video_id: int, model=None, ball_model=None, session_factory=SessionLocal) -> None:
    db: Session = session_factory()
    video = db.get(Video, video_id)
    if video is None:
        db.close()
        return

    file_path = video.file_path

    try:
        video.status = VideoStatus.PROCESSING
        video.match.status = MatchStatus.PROCESSING
        db.execute(delete(Detection).where(Detection.video_id == video_id))
        db.commit()

        device = resolve_inference_device()
        # Injected models (tests) behave like the generic model unless told otherwise.
        player_class_map = GENERIC_PLAYER_CLASS_MAP
        player_imgsz: int | None = None
        football_player_model = False

        if model is None:
            from ultralytics import YOLO

            if PLAYER_MODEL_PATH.exists():
                model = YOLO(str(PLAYER_MODEL_PATH))
                football_player_model = True
            else:
                print(
                    f"[detection] {PLAYER_MODEL_PATH.name} not found, falling back to generic yolov8n",
                    flush=True,
                )
                model = YOLO("yolov8n.pt")
            ball_model = YOLO(str(BALL_MODEL_PATH))
            model.to(device)
            ball_model.to(device)

        if football_player_model:
            player_class_map = FOOTBALL_PLAYER_CLASS_MAP
            player_imgsz = FOOTBALL_PLAYER_IMGSZ

        # FP16 roughly halves memory traffic and speeds up inference on the GPU;
        # it is only well supported on CUDA, so keep FP32 on the CPU fallback.
        # The generic yolov8n model is FP16-stable, but the specialised Roboflow
        # ball model regresses NaN box coordinates in half precision (every ball
        # box comes back non-finite). The Roboflow player model comes from the
        # same training setup, so it also stays in FP32 to be safe.
        player_half = device == "cuda" and not football_player_model
        ball_half = False
        print(
            f"[detection] player model: {'football' if football_player_model else 'generic'}, "
            f"batch size: {DETECTION_BATCH_SIZE}, "
            f"half precision: player={player_half} ball={ball_half}",
            flush=True,
        )

        with tempfile.TemporaryDirectory(prefix="football-frames-") as temp_dir:
            sample_interval = effective_sample_interval(
                settings.detection_sample_interval_seconds, video.fps
            )
            if sample_interval != settings.detection_sample_interval_seconds:
                print(
                    f"[detection] video is {video.fps:g} fps; sampling capped at "
                    f"{1 / sample_interval:g} fps instead of "
                    f"{1 / settings.detection_sample_interval_seconds:g}",
                    flush=True,
                )
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
            track_kwargs = {}
            if player_imgsz is not None:
                track_kwargs["imgsz"] = player_imgsz
            player_stream = model.track(
                source=temp_dir,
                stream=True,
                persist=True,
                tracker=TRACKER_CONFIG,
                classes=sorted(player_class_map),
                device=device,
                half=player_half,
                verbose=False,
                **track_kwargs,
            )
            track_started = time.perf_counter()
            for frame_number, result in enumerate(player_stream):
                pending.extend(
                    _result_rows(video_id, frame_number, sample_interval, result, player_class_map)
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

            # --- ball: sliced (tiled) detection, one ball per frame -----------
            if ball_model is not None:
                import cv2

                batch_size = DETECTION_BATCH_SIZE
                previous_center: tuple[float, float] | None = None
                ball_frames = 0
                for frame_number, frame_path in enumerate(frame_paths):
                    image = cv2.imread(frame_path)
                    if image is None:
                        continue
                    height, width = image.shape[:2]
                    tiles = ball_tiles(width, height)
                    crops = [image[y0:y1, x0:x1] for x0, y0, x1, y1 in tiles]
                    results: list = []
                    start = 0
                    while start < len(crops):
                        try:
                            results.extend(
                                ball_model(
                                    crops[start : start + batch_size],
                                    imgsz=BALL_TILE_SIZE,
                                    device=device,
                                    half=ball_half,
                                    verbose=False,
                                )
                            )
                        except Exception as exc:  # noqa: BLE001 - narrow to CUDA OOM below
                            if _is_cuda_oom(exc) and batch_size > 1:
                                batch_size = max(1, batch_size // 2)
                                _empty_cuda_cache(device)
                                print(
                                    f"[detection] CUDA out of memory, retrying with batch size {batch_size}",
                                    flush=True,
                                )
                                continue
                            raise
                        start += batch_size
                    chosen = select_ball(_ball_candidates(results, tiles), previous_center)
                    del results, crops, image
                    if chosen is not None:
                        box, conf = chosen
                        previous_center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
                        pending.append(_ball_row(video_id, frame_number, sample_interval, box, conf))
                        ball_frames += 1
                    flush()
                    if (frame_number + 1) % 500 == 0 or frame_number + 1 == frame_total:
                        print(
                            f"[detection] ball: {frame_number + 1}/{frame_total} frames, "
                            f"ball found in {ball_frames}",
                            flush=True,
                        )
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
    class_map: dict[int, str],
) -> list[dict]:
    """Turn one frame's boxes into ``Detection`` mapping dicts for a bulk insert.

    ``class_map`` maps model class ids to stored ``class_name`` values; boxes of
    any other class are dropped. Plain dicts (not ORM instances) keep the
    streaming insert path cheap: they never enter the session's identity map,
    so memory stays flat over a long video. ``result.boxes.id`` (present in
    track mode) becomes ``track_id``.
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
        class_name = class_map.get(int(class_id))
        if class_name is None:
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


def list_detections(video_id: int, db: Session) -> list[Detection]:
    return db.scalars(
        select(Detection).where(Detection.video_id == video_id).order_by(Detection.frame_timestamp, Detection.id)
    ).all()