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
    assert body["detections"][1]["frame_timestamp"] == 0.1


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


class FakeYoloModel:
    def __call__(self, frame_path: str, verbose: bool = False):
        assert Path(frame_path).suffix == ".jpg"
        return [
            SimpleNamespace(
                boxes=SimpleNamespace(
                    xyxy=FakeTensor([[10, 20, 60, 100]]),
                    conf=FakeTensor([0.91]),
                    cls=FakeTensor([0]),
                )
            )
        ]


class FakeBallYoloModel:
    def __call__(self, frame_path: str, verbose: bool = False):
        assert Path(frame_path).suffix == ".jpg"
        return [
            SimpleNamespace(
                boxes=SimpleNamespace(
                    xyxy=FakeTensor([[120, 80, 128, 88]]),
                    conf=FakeTensor([0.87]),
                    cls=FakeTensor([0]),
                )
            )
        ]


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