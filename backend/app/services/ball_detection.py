"""High-recall ball detection: full frame + overlapping tiles (SAHI-style).

A ball on a wide match camera is often only 8-15 px across, and a ball in
flight is additionally motion-blurred. Feeding the whole 1920 px frame to the
detector shrinks it to the model's input size and the ball can disappear
entirely. Here every frame is run once whole (for context and the ball close
to the camera) and once as a grid of overlapping tiles, each of which the
model sees at a much higher effective resolution. The boxes from all views are
mapped back to frame coordinates and merged with NMS.

The confidence threshold is intentionally low; ``ball_tracking`` removes the
resulting false positives by requiring a plausible trajectory.
"""

from collections.abc import Callable, Iterator

from app.services.ball_tracking import BallCandidate

# Neighbouring tiles overlap by this fraction of the tile size so that a ball
# sitting on a tile border is fully visible in at least one tile.
TILE_OVERLAP = 0.15
# Boxes from different views of the same ball overlap heavily; anything above
# this IoU is treated as a duplicate.
MERGE_IOU = 0.3


def tile_windows(width: int, height: int, grid: int) -> list[tuple[int, int, int, int]]:
    """Return ``grid x grid`` overlapping (x1, y1, x2, y2) windows covering the frame."""
    if grid <= 1:
        return []
    tile_w = int(width / (grid - (grid - 1) * TILE_OVERLAP))
    tile_h = int(height / (grid - (grid - 1) * TILE_OVERLAP))
    step_x = (width - tile_w) / (grid - 1)
    step_y = (height - tile_h) / (grid - 1)
    windows = []
    for row in range(grid):
        for col in range(grid):
            x1, y1 = int(round(col * step_x)), int(round(row * step_y))
            windows.append((x1, y1, min(width, x1 + tile_w), min(height, y1 + tile_h)))
    return windows


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def merge_boxes(boxes: list[tuple[float, float, float, float, float]]) -> list[tuple[float, ...]]:
    """Greedy NMS over (x1, y1, x2, y2, conf) boxes, highest confidence wins."""
    kept: list[tuple[float, ...]] = []
    for box in sorted(boxes, key=lambda b: b[4], reverse=True):
        if all(_iou(box, other) <= MERGE_IOU for other in kept):
            kept.append(box)
    return kept


def _boxes(result, offset_x: int, offset_y: int, class_id: int) -> list[tuple[float, ...]]:
    boxes = getattr(result, "boxes", None)
    if boxes is None:
        return []
    found = []
    for (x1, y1, x2, y2), conf, cls in zip(boxes.xyxy.tolist(), boxes.conf.tolist(), boxes.cls.tolist()):
        if int(cls) != class_id:
            continue
        found.append((x1 + offset_x, y1 + offset_y, x2 + offset_x, y2 + offset_y, float(conf)))
    return found


def detect_ball_candidates(
    infer: Callable[[list], list],
    frame_paths: list[str],
    *,
    class_id: int,
    tile_grid: int,
    batch_size: int,
    on_oom: Callable[[Exception, int], int | None] | None = None,
    read_image: Callable[[str], object] | None = None,
) -> Iterator[list[BallCandidate]]:
    """Yield one list of merged ball candidates per frame, in frame order.

    ``infer`` runs the ball model on a list of BGR images. ``on_oom`` receives
    a failed batch's exception and current batch size and returns a smaller
    batch size to retry with, or ``None`` to re-raise.
    """
    if read_image is None:
        import cv2

        read_image = cv2.imread

    base = 0
    while base < len(frame_paths):
        batch_paths = frame_paths[base : base + batch_size]
        images, views = [], []
        for index, path in enumerate(batch_paths):
            image = read_image(path)
            if image is None:
                raise RuntimeError(f"could not read frame {path}")
            height, width = image.shape[:2]
            images.append(image)
            views.append((index, 0, 0))
            for x1, y1, x2, y2 in tile_windows(width, height, tile_grid):
                images.append(image[y1:y2, x1:x2])
                views.append((index, x1, y1))
        try:
            results = infer(images)
        except Exception as exc:  # noqa: BLE001 - the caller decides which errors to retry
            smaller = on_oom(exc, batch_size) if on_oom is not None else None
            if smaller is None:
                raise
            batch_size = smaller
            continue

        per_frame: list[list[tuple[float, ...]]] = [[] for _ in batch_paths]
        for (index, offset_x, offset_y), result in zip(views, results):
            per_frame[index].extend(_boxes(result, offset_x, offset_y, class_id))
        del images, results
        for index, boxes in enumerate(per_frame):
            yield [BallCandidate(base + index, *box) for box in merge_boxes(boxes)]
        base += len(batch_paths)
