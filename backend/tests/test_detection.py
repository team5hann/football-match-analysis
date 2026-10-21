from pathlib import Path
from types import SimpleNamespace

from app.services.detection import run_detection


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
    assert body["detections"][1]["frame_timestamp"] == 0.033
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
    balls = [detection for detection in detections if detection["class"] == "ball"]
    players = [detection for detection in detections if detection["class"] == "player"]
    # Exactly one ball per sampled frame, always the real one (the jumping
    # false positive is rejected by the trajectory filter).
    assert len(balls) == len(players)
    assert len({ball["frame_timestamp"] for ball in balls}) == len(balls)
    assert all(ball["bounding_box"] == {"x": 120.0, "y": 80.0, "width": 8.0, "height": 8.0} for ball in balls)
    assert not any(ball["is_interpolated"] for ball in balls)


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
        paths = source if isinstance(source, list) else [source]
        return [_fake_result(self.box) for frame_path in paths if Path(frame_path).suffix == ".jpg"]


class FakeBallYoloModel(FakeYoloModel):
    """The ball model now receives decoded images (full frame + tiles). It
    reports the ball only on the full 320x240 frame, plus a weak false positive
    that jumps around and must be filtered out by the trajectory cleaner."""

    box = ([120, 80, 128, 88], 0.87, 0)

    def __init__(self):
        self.calls = 0

    def __call__(self, source, verbose: bool = False, **kwargs):
        results = []
        for image in source:
            if image.shape[:2] == (240, 320):
                self.calls += 1
                noise_x = 20 + (self.calls * 97) % 260
                results.append(
                    SimpleNamespace(
                        boxes=SimpleNamespace(
                            xyxy=FakeTensor([self.box[0], [noise_x, 200, noise_x + 6, 206]]),
                            conf=FakeTensor([self.box[1], 0.08]),
                            cls=FakeTensor([0, 0]),
                            id=None,
                        )
                    )
                )
            else:
                results.append(SimpleNamespace(boxes=None))
        return results


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