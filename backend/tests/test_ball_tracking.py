from types import SimpleNamespace

import numpy as np

from app.services.ball_detection import detect_ball_candidates, merge_boxes, tile_windows
from app.services.ball_tracking import BallCandidate, clean_ball_trajectory

WIDTH = 1920


def _ball(frame, x, y, conf=0.6, size=12):
    return BallCandidate(frame, x, y, x + size, y + size, conf)


def test_flying_ball_gap_is_interpolated():
    # Ball flies right 30 px/frame; detector misses frames 4-6 (blur).
    candidates = [_ball(f, 100 + 30 * f, 500) for f in range(10) if f not in (4, 5, 6)]

    trajectory = clean_ball_trajectory(candidates, frame_width=WIDTH, max_gap_frames=5)

    assert [p.frame for p in trajectory] == list(range(10))
    filled = [p for p in trajectory if p.interpolated]
    assert [p.frame for p in filled] == [4, 5, 6]
    assert [round(p.x1) for p in filled] == [220, 250, 280]
    assert all(p.confidence == 0.0 for p in filled)


def test_one_ball_per_frame_and_false_positives_rejected():
    real = [_ball(f, 400 + 20 * f, 300 + 5 * f, conf=0.5) for f in range(20)]
    # A static pitch marking hit at very low confidence, and random one-off hits.
    marking = [_ball(f, 1500, 900, conf=0.07) for f in range(20)]
    noise = [_ball(f, (f * 431) % 1800, (f * 197) % 1000, conf=0.2) for f in range(0, 20, 3)]

    trajectory = clean_ball_trajectory(real + marking + noise, frame_width=WIDTH, max_gap_frames=5)

    assert len({p.frame for p in trajectory}) == len(trajectory) == 20
    assert all(abs(p.x1 - (400 + 20 * p.frame)) < 1e-6 for p in trajectory)


def test_stronger_track_wins_when_two_overlap():
    weak = [_ball(f, 100 + 10 * f, 100, conf=0.2) for f in range(10)]
    strong = [_ball(f, 1000 - 10 * f, 700, conf=0.8) for f in range(10)]

    trajectory = clean_ball_trajectory(weak + strong, frame_width=WIDTH, max_gap_frames=5)

    assert all(p.y1 == 700 for p in trajectory)


def test_implausible_jump_and_long_gap_not_interpolated():
    first = [_ball(f, 100 + 10 * f, 500) for f in range(5)]
    # Jumps across the whole frame after a gap: a different object, not the ball in flight.
    second = [_ball(f, 1800, 100) for f in range(7, 12)]
    # Same position again but after a gap longer than allowed.
    third = [_ball(f, 1800, 100) for f in range(30, 35)]

    trajectory = clean_ball_trajectory(first + second + third, frame_width=WIDTH, max_gap_frames=5)

    assert not any(p.interpolated for p in trajectory)
    assert len(trajectory) == 15


def test_empty_input():
    assert clean_ball_trajectory([], frame_width=WIDTH, max_gap_frames=5) == []


def test_tile_windows_cover_frame_with_overlap():
    windows = tile_windows(1920, 1080, 2)

    assert len(windows) == 4
    assert min(w[0] for w in windows) == 0 and max(w[2] for w in windows) == 1920
    assert min(w[1] for w in windows) == 0 and max(w[3] for w in windows) == 1080
    left, right = windows[0], windows[1]
    assert right[0] < left[2]  # horizontal overlap
    assert tile_windows(1920, 1080, 1) == []


def test_merge_boxes_removes_duplicates_keeps_best():
    merged = merge_boxes([(10, 10, 20, 20, 0.4), (11, 11, 21, 21, 0.9), (500, 500, 510, 510, 0.3)])

    assert merged == [(11, 11, 21, 21, 0.9), (500, 500, 510, 510, 0.3)]


def _result(boxes):
    return SimpleNamespace(
        boxes=SimpleNamespace(
            xyxy=SimpleNamespace(tolist=lambda: [b[:4] for b in boxes]),
            conf=SimpleNamespace(tolist=lambda: [b[4] for b in boxes]),
            cls=SimpleNamespace(tolist=lambda: [0 for _ in boxes]),
        )
    )


def test_detect_ball_candidates_maps_tile_boxes_to_frame_coordinates():
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    windows = tile_windows(1920, 1080, 2)
    bottom_right = windows[3]

    def infer(images):
        # Ball found only in the bottom-right tile, at (10, 20) inside it.
        assert len(images) == 5
        results = [_result([]) for _ in images]
        results[4] = _result([(10, 20, 22, 32, 0.3)])
        return results

    frames = list(
        detect_ball_candidates(
            infer, ["a.jpg"], class_id=0, tile_grid=2, batch_size=4, read_image=lambda _path: frame
        )
    )

    assert len(frames) == 1 and len(frames[0]) == 1
    candidate = frames[0][0]
    assert (candidate.x1, candidate.y1) == (bottom_right[0] + 10, bottom_right[1] + 20)
    assert candidate.frame == 0


def test_detect_ball_candidates_retries_with_smaller_batch_on_oom():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    batch_sizes = []

    def infer(images):
        batch_sizes.append(len(images))
        if len(images) > 2:
            raise RuntimeError("CUDA out of memory")
        return [_result([(1, 1, 5, 5, 0.9)]) for _ in images]

    frames = list(
        detect_ball_candidates(
            infer,
            [f"{i}.jpg" for i in range(5)],
            class_id=0,
            tile_grid=1,
            batch_size=4,
            on_oom=lambda exc, size: max(1, size // 2) if "out of memory" in str(exc) else None,
            read_image=lambda _path: frame,
        )
    )

    assert [c[0].frame for c in frames] == [0, 1, 2, 3, 4]
    assert batch_sizes[0] == 4 and max(batch_sizes[1:]) == 2
