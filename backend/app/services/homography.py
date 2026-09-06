"""Per-frame pitch homography from detected pitch keypoints.

Replaces the old "stretch the min/max spread of box centres onto 105x68 m"
scaling with a real image->pitch projective transform.

The clip is a Veo "Follow-cam" export: a digitally cropped/zoomed window over a
static panorama, so it behaves as if the camera pans and zooms. A single
homography for the whole video is therefore invalid - every sampled frame gets
its own, computed from that frame's keypoints. This is a heavy extra pass: it
re-decodes frames and runs a large YOLOv8x-pose model, so keypoints are sampled
coarser than detections (``homography_sample_interval_seconds``) and in-between
detections reuse the nearest earlier frame's homography. Frames whose keypoints
are too few for a stable fit get ``status='no_homography'`` and their detections
keep ``pitch_x/pitch_y = NULL`` (callers fall back to the old scaling).

Model: martinjolif/yolo-football-pitch-detection (YOLOv8x-pose, 32 keypoints),
downloaded at image build like the specialised ball model.
"""
import tempfile
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.detection import Detection
from app.models.homography import FrameHomography
from app.models.video import Video
from app.services.detection import extract_frames, resolve_inference_device

settings = get_settings()

PITCH_MODEL_PATH = Path(__file__).resolve().parents[2] / "models" / "football-pitch-detection.pt"
PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0

# Minimum keypoints for a homography; 4 is the theoretical minimum for a
# projective fit, we also require that many RANSAC inliers.
MIN_KEYPOINTS = 4
# Keypoints below this model confidence are treated as "not seen".
KEYPOINT_CONF_THRESHOLD = 0.5
# The pose model is large (YOLOv8x); keep batches small.
HOMOGRAPHY_BATCH_SIZE = 4

# The 32 pitch keypoints of the roboflow "football-field-detection" layout the
# model was trained on, given there in cm on a 120 x 70 m pitch. We rescale to
# the project's standard 105 x 68 m so the homography maps straight to metres.
_ROBOFLOW_VERTICES_CM = [
    (0, 0), (0, 1450), (0, 2584), (0, 4416), (0, 5550), (0, 7000),
    (550, 2584), (550, 4416), (1100, 3500),
    (2015, 1450), (2015, 2584), (2015, 4416), (2015, 5550),
    (6000, 0), (6000, 2585), (6000, 4415), (6000, 7000),
    (9985, 1450), (9985, 2584), (9985, 4416), (9985, 5550),
    (10900, 3500),
    (11450, 2584), (11450, 4416),
    (12000, 0), (12000, 1450), (12000, 2584), (12000, 4416), (12000, 5550), (12000, 7000),
    (5085, 3500), (6915, 3500),
]
_SCALE_X = PITCH_LENGTH_M / 120.0
_SCALE_Y = PITCH_WIDTH_M / 70.0
PITCH_KEYPOINTS_M: list[tuple[float, float]] = [
    (x_cm / 100.0 * _SCALE_X, y_cm / 100.0 * _SCALE_Y) for x_cm, y_cm in _ROBOFLOW_VERTICES_CM
]


def feet_point(box: dict[str, float]) -> tuple[float, float]:
    """Ground-contact point of a player box (bottom centre)."""
    return box["x"] + box["width"] / 2.0, box["y"] + box["height"]


def center_point(box: dict[str, float]) -> tuple[float, float]:
    return box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0


def compute_homography(
    keypoints_xy,
    keypoints_conf,
    *,
    min_points: int = MIN_KEYPOINTS,
    conf_threshold: float = KEYPOINT_CONF_THRESHOLD,
) -> tuple[list[list[float]] | None, str, int]:
    """Fit an image->pitch(metres) homography from one frame's keypoints.

    ``keypoints_xy``: iterable of (x, y) pixel coords, index = pitch keypoint id.
    ``keypoints_conf``: matching per-keypoint confidences.
    Returns ``(matrix_3x3 | None, status, inlier_count)`` - status is ``"ok"``
    or ``"no_homography"``.
    """
    import cv2
    import numpy as np

    src: list[tuple[float, float]] = []
    dst: list[tuple[float, float]] = []
    for index in range(min(len(keypoints_xy), len(PITCH_KEYPOINTS_M))):
        x, y = keypoints_xy[index]
        conf = float(keypoints_conf[index]) if index < len(keypoints_conf) else 0.0
        if conf < conf_threshold or (x <= 0 and y <= 0):
            continue
        src.append((float(x), float(y)))
        dst.append(PITCH_KEYPOINTS_M[index])

    if len(src) < min_points:
        return None, "no_homography", len(src)

    matrix, mask = cv2.findHomography(
        np.asarray(src, dtype=np.float64), np.asarray(dst, dtype=np.float64), cv2.RANSAC, 5.0
    )
    if matrix is None or not np.all(np.isfinite(matrix)):
        return None, "no_homography", len(src)
    inliers = int(mask.sum()) if mask is not None else len(src)
    if inliers < min_points:
        return None, "no_homography", inliers
    return matrix.tolist(), "ok", inliers


def project_point(matrix, x: float, y: float) -> tuple[float, float] | None:
    """Apply a 3x3 homography to a pixel point, returning metres or None."""
    import numpy as np

    vector = np.asarray(matrix, dtype=np.float64) @ np.array([x, y, 1.0])
    if abs(vector[2]) < 1e-9:
        return None
    projected = (float(vector[0] / vector[2]), float(vector[1] / vector[2]))
    if not all(np.isfinite(projected)):
        return None
    return projected


def _parse_keypoints(result):
    """Extract (xy, conf) for the most confident detected pitch instance."""
    import numpy as np

    keypoints = getattr(result, "keypoints", None)
    if keypoints is None or keypoints.xy is None:
        return None, None
    xy = keypoints.xy
    xy = xy.cpu().numpy() if hasattr(xy, "cpu") else np.asarray(xy)
    if xy.shape[0] == 0:
        return None, None
    conf = keypoints.conf
    if conf is None:
        conf = np.ones(xy.shape[:2])
    else:
        conf = conf.cpu().numpy() if hasattr(conf, "cpu") else np.asarray(conf)
    best = int(conf.mean(axis=1).argmax())
    return xy[best], conf[best]


# Homography frames whose projections of the same pixel differ by more than
# this are treated as a camera cut/fast pan - we do NOT interpolate across them.
_CUT_DISTANCE_M = 25.0
# Projections landing well outside the pitch are a bad fit for that detection,
# not a real position - drop them (the detection keeps NULL pitch coords and
# downstream falls back to the old scaling) rather than clamp and distort
# team-shape metrics.
_PITCH_MARGIN_M = 12.0


def _homography_bracket(valid_frames: list[tuple[float, list]], timestamp: float, tolerance: float):
    """Return ``((ts0, H0), (ts1, H1) | None)`` bracketing ``timestamp``.

    ``H0`` is the last homography at/before the timestamp, ``H1`` the first
    after it (for interpolation). ``None`` if nothing is close enough.
    """
    before = None
    after = None
    for frame_ts, matrix in valid_frames:
        if frame_ts <= timestamp + 1e-6:
            before = (frame_ts, matrix)
        else:
            after = (frame_ts, matrix)
            break
    if before is None:
        # before the first homography frame - only usable if very close
        if after is not None and after[0] - timestamp <= tolerance:
            return after, None
        return None
    if timestamp - before[0] > tolerance:
        return None
    return before, after


def _project_interpolated(bracket, point: tuple[float, float], timestamp: float):
    """Project ``point`` using the bracketing homographies.

    In-between detections reuse a coarsely-sampled homography; linearly blending
    the projections of the two nearest keypoint frames removes the position
    jump at window boundaries while the camera pans. Blending is skipped across
    a detected cut (the two projections disagree by > _CUT_DISTANCE_M).
    """
    (ts0, matrix0), after = bracket
    projected0 = project_point(matrix0, *point)
    if projected0 is None:
        return None
    if after is None:
        return projected0
    ts1, matrix1 = after
    projected1 = project_point(matrix1, *point)
    if projected1 is None or ts1 <= ts0:
        return projected0
    if abs(projected1[0] - projected0[0]) + abs(projected1[1] - projected0[1]) > _CUT_DISTANCE_M:
        return projected0  # camera cut - hold the earlier frame, don't average across it
    fraction = min(1.0, max(0.0, (timestamp - ts0) / (ts1 - ts0)))
    return (
        projected0[0] + (projected1[0] - projected0[0]) * fraction,
        projected0[1] + (projected1[1] - projected0[1]) * fraction,
    )


def _project_detections(
    db: Session, video_id: int, frames: list[tuple[float, list | None]], sample_interval: float
) -> int:
    valid = sorted((ts, m) for ts, m in frames if m is not None)
    tolerance = 2.0 * sample_interval
    updated = 0
    detections = db.scalars(
        select(Detection).where(Detection.video_id == video_id).order_by(Detection.frame_timestamp, Detection.id)
    ).all()
    for detection in detections:
        bracket = _homography_bracket(valid, detection.frame_timestamp, tolerance)
        if bracket is None:
            detection.pitch_x = None
            detection.pitch_y = None
            continue
        point = feet_point(detection.bounding_box) if detection.class_name == "player" else center_point(detection.bounding_box)
        projected = _project_interpolated(bracket, point, detection.frame_timestamp)
        if projected is None or not (
            -_PITCH_MARGIN_M <= projected[0] <= PITCH_LENGTH_M + _PITCH_MARGIN_M
            and -_PITCH_MARGIN_M <= projected[1] <= PITCH_WIDTH_M + _PITCH_MARGIN_M
        ):
            detection.pitch_x = None
            detection.pitch_y = None
            continue
        detection.pitch_x = round(projected[0], 3)
        detection.pitch_y = round(projected[1], 3)
        updated += 1
    return updated


def run_pitch_homography(
    video_id: int,
    model=None,
    sample_interval: float | None = None,
    session_factory=SessionLocal,
) -> dict:
    """Detect pitch keypoints per sampled frame, fit a homography, and stamp
    every detection of the video with real pitch coordinates.

    Additional pass on top of detection - it does NOT re-run object detection,
    but it does re-decode the video frames and run the pose model, so it is not
    free. Idempotent: previous ``frame_homographies`` rows are replaced and
    ``pitch_x/pitch_y`` recomputed.
    """
    db: Session = session_factory()
    video = db.get(Video, video_id)
    if video is None:
        db.close()
        raise ValueError("Video not found")

    try:
        detection_count = db.scalar(
            select(func.count(Detection.id)).where(Detection.video_id == video_id)
        )
        if not detection_count:
            raise ValueError("Run detection before pitch homography")

        if sample_interval is None:
            sample_interval = settings.homography_sample_interval_seconds

        device = resolve_inference_device()
        if model is None:
            from ultralytics import YOLO

            model = YOLO(str(PITCH_MODEL_PATH))
            model.to(device)
        # FP32 on purpose: the specialised football weights have shown FP16
        # numerical issues, and keypoint regression is precision-sensitive.
        print(
            f"[homography] pitch keypoints every {sample_interval}s on {device} (FP32), "
            f"min {MIN_KEYPOINTS} points",
            flush=True,
        )

        db.execute(delete(FrameHomography).where(FrameHomography.video_id == video_id))
        db.commit()

        frames_result: list[tuple[float, list | None]] = []
        ok = 0
        with tempfile.TemporaryDirectory(prefix="football-homography-") as temp_dir:
            frame_paths = extract_frames(video.file_path, Path(temp_dir), sample_interval)
            for start in range(0, len(frame_paths), HOMOGRAPHY_BATCH_SIZE):
                chunk = [str(path) for path in frame_paths[start : start + HOMOGRAPHY_BATCH_SIZE]]
                results = model(chunk, device=device, half=False, verbose=False)
                for offset, result in enumerate(results):
                    timestamp = round((start + offset) * sample_interval, 6)
                    keypoints_xy, keypoints_conf = _parse_keypoints(result)
                    if keypoints_xy is None:
                        matrix, status, count = None, "no_homography", 0
                    else:
                        matrix, status, count = compute_homography(keypoints_xy, keypoints_conf)
                    db.add(
                        FrameHomography(
                            video_id=video_id,
                            frame_timestamp=timestamp,
                            status=status,
                            keypoint_count=count,
                            matrix=matrix,
                        )
                    )
                    frames_result.append((timestamp, matrix))
                    ok += status == "ok"
            db.commit()

        projected = _project_detections(db, video_id, frames_result, sample_interval)
        db.commit()

        return {
            "video_id": video_id,
            "sample_interval_seconds": sample_interval,
            "frames_processed": len(frames_result),
            "frames_with_homography": ok,
            "frames_without_homography": len(frames_result) - ok,
            "detections_projected": projected,
            "detections_total": detection_count,
            "note": (
                "Per-frame homography from detected pitch keypoints. Frames without "
                "enough keypoints keep pitch_x/pitch_y = NULL and downstream falls "
                "back to the old image-linear scaling."
            ),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def read_homography_status(db: Session, video_id: int) -> dict:
    rows = db.scalars(
        select(FrameHomography).where(FrameHomography.video_id == video_id)
    ).all()
    ok = sum(1 for row in rows if row.status == "ok")
    projected = db.scalar(
        select(func.count(Detection.id)).where(
            Detection.video_id == video_id, Detection.pitch_x.is_not(None)
        )
    )
    total = db.scalar(select(func.count(Detection.id)).where(Detection.video_id == video_id))
    return {
        "video_id": video_id,
        "frames_processed": len(rows),
        "frames_with_homography": ok,
        "frames_without_homography": len(rows) - ok,
        "detections_projected": projected or 0,
        "detections_total": total or 0,
    }
