from collections.abc import Iterable


def build_heatmap(
    detections: Iterable[dict],
    image_width: int,
    image_height: int,
    grid_width: int = 20,
    grid_height: int = 12,
) -> list[list[int]]:
    """Aggregate player box centers into a camera-coordinate occupancy grid."""
    grid = [[0 for _ in range(grid_width)] for _ in range(grid_height)]
    if image_width <= 0 or image_height <= 0:
        return grid

    for detection in detections:
        box = detection["bounding_box"]
        center_x = (box["x"] + box["width"] / 2) / image_width
        center_y = (box["y"] + box["height"] / 2) / image_height
        column = min(grid_width - 1, max(0, int(center_x * grid_width)))
        row = min(grid_height - 1, max(0, int(center_y * grid_height)))
        grid[row][column] += 1
    return grid