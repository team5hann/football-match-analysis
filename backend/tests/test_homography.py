"""Sanity tests for pitch homography maths and fallback behaviour."""
import numpy as np

from app.services.homography import (
    MIN_KEYPOINTS,
    PITCH_KEYPOINTS_M,
    compute_homography,
    project_point,
    _parse_keypoints,
)

_PX_PER_M = 12.0  # a simple scale-only "camera": 1 metre -> 12 pixels


def _keypoint_arrays(indices):
    """Build (32,2) xy + (32,) conf where `indices` are 'seen' at 12 px/m."""
    xy = np.zeros((32, 2), dtype=np.float64)
    conf = np.zeros(32, dtype=np.float64)
    for index in indices:
        pitch_x, pitch_y = PITCH_KEYPOINTS_M[index]
        xy[index] = (pitch_x * _PX_PER_M + 5.0, pitch_y * _PX_PER_M + 5.0)
        conf[index] = 0.95
    return xy, conf


def test_square_projection_recovers_known_points():
    seen = [0, 5, 25, 30, 13, 16]  # 4 pitch corners + centre line + a mid point
    xy, conf = _keypoint_arrays(seen)

    matrix, status, inliers = compute_homography(xy, conf)

    assert status == "ok"
    assert inliers >= MIN_KEYPOINTS
    # a keypoint that was NOT used for the fit still projects back onto its
    # real pitch coordinate (within a few centimetres)
    held_out_index = 9
    px = (
        PITCH_KEYPOINTS_M[held_out_index][0] * _PX_PER_M + 5.0,
        PITCH_KEYPOINTS_M[held_out_index][1] * _PX_PER_M + 5.0,
    )
    projected = project_point(matrix, *px)
    assert projected is not None
    assert abs(projected[0] - PITCH_KEYPOINTS_M[held_out_index][0]) < 0.05
    assert abs(projected[1] - PITCH_KEYPOINTS_M[held_out_index][1]) < 0.05
    # projected points sit inside the 105 x 68 m pitch
    assert 0 <= projected[0] <= 105 and 0 <= projected[1] <= 68


def test_too_few_keypoints_falls_back_without_crashing():
    xy, conf = _keypoint_arrays([0, 5, 25])  # only 3

    matrix, status, count = compute_homography(xy, conf)

    assert matrix is None
    assert status == "no_homography"
    assert count == 3


def test_no_keypoints_returns_no_homography():
    xy = np.zeros((32, 2))
    conf = np.zeros(32)
    matrix, status, count = compute_homography(xy, conf)
    assert matrix is None and status == "no_homography" and count == 0


def test_low_confidence_keypoints_are_ignored():
    xy, conf = _keypoint_arrays([0, 5, 25, 30, 13])
    conf[:] = 0.10  # every keypoint below threshold
    matrix, status, count = compute_homography(xy, conf)
    assert matrix is None and status == "no_homography" and count == 0


def test_parse_keypoints_handles_missing_keypoints():
    class _Result:
        keypoints = None

    assert _parse_keypoints(_Result()) == (None, None)


def test_project_point_returns_none_for_degenerate_row():
    degenerate = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]]
    assert project_point(degenerate, 10.0, 10.0) is None
