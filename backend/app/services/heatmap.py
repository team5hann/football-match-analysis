from collections.abc import Iterable

PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0


def build_heatmap(
    detections: Iterable[dict],
    image_width: int,
    image_height: int,
    grid_width: int = 20,
    grid_height: int = 12,
) -> list[list[int]]:
    """Aggregate player positions into an occupancy grid.

    Uses the real pitch position (``pitch`` = ``(x_m, y_m)`` from the per-frame
    homography) when present on a detection, so cells map to true pitch areas;
    otherwise falls back to the box centre over the image size (old behaviour).
    """
    grid = [[0 for _ in range(grid_width)] for _ in range(grid_height)]
    if image_width <= 0 or image_height <= 0:
        return grid

    for detection in detections:
        pitch = detection.get("pitch")
        if pitch is not None and pitch[0] is not None and pitch[1] is not None:
            center_x = min(1.0, max(0.0, pitch[0] / PITCH_LENGTH_M))
            center_y = min(1.0, max(0.0, pitch[1] / PITCH_WIDTH_M))
        else:
            box = detection["bounding_box"]
            center_x = (box["x"] + box["width"] / 2) / image_width
            center_y = (box["y"] + box["height"] / 2) / image_height
        column = min(grid_width - 1, max(0, int(center_x * grid_width)))
        row = min(grid_height - 1, max(0, int(center_y * grid_height)))
        grid[row][column] += 1
    return grid