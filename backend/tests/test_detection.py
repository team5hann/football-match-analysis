from pathlib import Path
from types import SimpleNamespace

from app.services.detection import (
    FOOTBALL_PLAYER_CLASS_MAP,
    _ball_candidates,
    _result_rows,
    ball_tiles,
    effective_sample_interval,
    run_detection,
    select_ball,
)


def test_detection_status_endpoint_returns_string_status(client):
    match = client.post("/api/matches", json={}).json()
    video = client.post(
        f"/api/matches/{match['id']}/video",
        files={"file": ("clip.mp4", b"not-a-video", "video/mp4")},
    )
    assert video.status_code == 201

    response = client.get(f"/api/videos/{video.json()['id']}/detection")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"


def test_detection_stores_player_detections(client, tmp_path):
    match = client.post("/api/matches", json={}).json()
    video_path = _make_test_video(tmp_path)
    with video_path.open("rb") as video_file:
        response = client.post(
            f"/api/matches/{match['id']}/video",
            files={"file": ("clip.mp4", video_file, "video/mp4")},
        )
    assert response.status_code == 201, response.text

    run_detection(response.json()["id"], model=FakeYoloModel())

    result = client.get(f"/api/videos/{response.json()['id']}/detection")
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "analyzed"
    assert body["detections_count"] >= 1
    assert body["detections"][0]["class"] == "player"
    assert body["detections"][0]["bounding_box"] == {"x": 10.0, "y": 20.0, "width": 50.0, "height": 80.0}
    # The test clip is 10 fps, so the ~30 fps default is capped at the source
    # rate instead of duplicating frames.
    assert body["detections"][1]["frame_timestamp"] == 0.1
    # track_id now comes straight from the (fake) ByteTrack stream
    assert body["detections"][0]["track_id"] == 1


def test_detection_stores_specialized_ball_detections(client, tmp_path):
    match = client.post("/api/matches", json={}).json()
    video_path = _make_test_video(tmp_path)
    with video_path.open("rb") as video_file:
        response = client.post(
            f"/api/matches/{match['id']}/video",
            files={"file": ("clip.mp4", video_file, "video/mp4")},
        )
    assert response.status_code == 201, response.text

    run_detection(
        response.json()["id"],
        model=FakeYoloModel(),
        ball_model=FakeBallYoloModel(),
    )

    detections = client.get(f"/api/videos/{response.json()['id']}/detection").json()["detections"]
    assert {detection["class"] for detection in detections} == {"player", "ball"}


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


def _fake_result(box, track_id=None):
    xyxy, conf, cls = box
    return SimpleNamespace(
        boxes=SimpleNamespace(
            xyxy=FakeTensor([xyxy]),
            conf=FakeTensor([conf]),
            cls=FakeTensor([cls]),
            id=FakeTensor([track_id]) if track_id is not None else None,
        )
    )


class FakeYoloModel:
    """Stands in for a tracking YOLO model: .track() yields one result per frame
    with a stable ByteTrack-style id."""

    box = ([10, 20, 60, 100], 0.91, 0)

    def to(self, device: str):
        return self

    def track(self, source, stream=False, persist=False, **kwargs):
        # Real code now hands Ultralytics the frames *directory* (never a list),
        # so mirror that: expand a directory to its sorted .jpg frames.
        if isinstance(source, list):
            paths = source
        elif Path(source).is_dir():
            paths = sorted(Path(source).glob("*.jpg"))
        else:
            paths = [source]
        assert paths, f"no frames found for source {source!r}"
        for frame_path in paths:
            assert Path(frame_path).suffix == ".jpg"
            yield _fake_result(self.box, track_id=1)

    def __call__(self, source, verbose: bool = False, **kwargs):
        items = source if isinstance(source, list) else [source]
        return [_fake_result(self.box) for _item in items]


class FakeBallYoloModel(FakeYoloModel):
    # The ball model now receives in-memory tile crops, one result per tile.
    box = ([120, 80, 128, 88], 0.87, 0)


def test_effective_sample_interval_caps_at_video_fps():
    assert effective_sample_interval(0.033, 25.0) == 0.04
    assert effective_sample_interval(0.0167, 60.0) == 0.0167
    assert effective_sample_interval(0.2, 30.0) == 0.2
    assert effective_sample_interval(0.033, None) == 0.033


def test_ball_tiles_cover_full_hd_frame():
    tiles = ball_tiles(1920, 1080)
    assert all(x1 - x0 == 640 and y1 - y0 == 640 for x0, y0, x1, y1 in tiles)
    assert max(x1 for _, _, x1, _ in tiles) == 1920
    assert max(y1 for _, _, _, y1 in tiles) == 1080
    assert {(x0, y0) for x0, y0, _, _ in tiles} >= {(0, 0), (1280, 440)}
    # Frames smaller than a tile become a single tile.
    assert ball_tiles(320, 240) == [(0, 0, 320, 240)]


def test_select_ball_prefers_candidate_near_previous_ball():
    near = ([100, 100, 110, 110], 0.4)
    far_but_confident = ([900, 500, 910, 510], 0.9)
    assert select_ball([near, far_but_confident], previous_center=(104, 104)) == near
    # Without history (or if nothing is near) the most confident wins.
    assert select_ball([near, far_but_confident], previous_center=None) == far_but_confident
    assert select_ball([far_but_confident], previous_center=(104, 104)) == far_but_confident
    assert select_ball([([0, 0, float("nan"), 1], 0.9)], previous_center=None) is None


def test_ball_candidates_shift_tile_boxes_to_frame_coordinates():
    tiles = [(0, 0, 640, 640), (512, 440, 1152, 1080)]
    results = [_fake_result(([10, 10, 20, 20], 0.5, 0)), _fake_result(([10, 10, 20, 20], 0.7, 0))]
    assert _ball_candidates(results, tiles) == [
        ([10, 10, 20, 20], 0.5),
        ([522, 450, 532, 460], 0.7),
    ]


def test_result_rows_maps_football_classes_and_drops_others():
    result = SimpleNamespace(
        boxes=SimpleNamespace(
            xyxy=FakeTensor([[0, 0, 10, 20], [5, 5, 15, 25], [1, 1, 3, 3], [2, 2, 12, 22]]),
            conf=FakeTensor([0.9, 0.8, 0.7, 0.6]),
            cls=FakeTensor([2, 1, 0, 3]),  # player, goalkeeper, ball, referee
            id=FakeTensor([1, 2, 3, 4]),
        )
    )
    rows = _result_rows(1, 0, 0.1, result, FOOTBALL_PLAYER_CLASS_MAP)
    assert [row["class_name"] for row in rows] == ["player", "player", "referee"]
    assert [row["track_id"] for row in rows] == [1, 2, 4]


def _make_test_video(tmp_path: Path) -> Path:
    import subprocess

    output = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output