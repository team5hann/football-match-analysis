from app.services.heatmap import build_heatmap


def test_heatmap_returns_requested_grid_shape_and_counts():
    grid = build_heatmap(
        [
            {"bounding_box": {"x": 0, "y": 0, "width": 10, "height": 10}},
            {"bounding_box": {"x": 90, "y": 90, "width": 10, "height": 10}},
            {"bounding_box": {"x": 90, "y": 90, "width": 10, "height": 10}},
        ],
        image_width=100,
        image_height=100,
        grid_width=20,
        grid_height=12,
    )

    assert len(grid) == 12
    assert all(len(row) == 20 for row in grid)
    assert sum(sum(row) for row in grid) == 3
    assert grid[0][1] == 1
    assert grid[11][19] == 2