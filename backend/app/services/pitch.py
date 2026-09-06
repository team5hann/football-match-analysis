"""Shared helpers for turning a detection into a pitch position.

Every spatial service used to do the same thing: take the box centre, divide by
the image size, and multiply by 105 x 68 m - i.e. assume the visible frame maps
linearly onto the whole pitch. That is wrong for a panning/zooming Veo crop.

Now, when the pitch-homography pass has run, each detection record carries a
``pitch`` = ``(x_m, y_m)`` already projected through that frame's homography
(feet point for players, box centre for the ball). These helpers prefer it and
fall back to the old image-linear scaling only when it is missing.
"""
PITCH_LENGTH_M = 105.0
PITCH_WIDTH_M = 68.0


def _box_center(box: dict[str, float]) -> tuple[float, float]:
    return box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0


def pitch_xy(record: dict, image_width: int, image_height: int) -> tuple[float, float]:
    """Real pitch position in metres (0..105, 0..68)."""
    pitch = record.get("pitch")
    if pitch is not None and pitch[0] is not None and pitch[1] is not None:
        return float(pitch[0]), float(pitch[1])
    center_x, center_y = _box_center(record["box"])
    return (
        center_x / max(image_width, 1) * PITCH_LENGTH_M,
        center_y / max(image_height, 1) * PITCH_WIDTH_M,
    )


def pitch_norm(record: dict, image_width: int, image_height: int) -> tuple[float, float]:
    """Pitch position normalised to 0..1 of the pitch, for grid/graph layout."""
    pitch = record.get("pitch")
    if pitch is not None and pitch[0] is not None and pitch[1] is not None:
        return (
            min(1.0, max(0.0, float(pitch[0]) / PITCH_LENGTH_M)),
            min(1.0, max(0.0, float(pitch[1]) / PITCH_WIDTH_M)),
        )
    center_x, center_y = _box_center(record["box"])
    return center_x / max(image_width, 1), center_y / max(image_height, 1)


def has_pitch(record: dict) -> bool:
    pitch = record.get("pitch")
    return pitch is not None and pitch[0] is not None and pitch[1] is not None
