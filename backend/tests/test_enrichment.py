from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from app.services.enrichment import assign_cluster_roles, run_enrichment


def test_assign_cluster_roles_matches_home_color_and_rejects_unclear_cluster():
    assignments = assign_cluster_roles(
        {0: [220, 35, 35], 1: [120, 120, 120]},
        "#FF0000",
        "#0000FF",
    )

    assert assignments[0].role == "home"
    assert assignments[0].similarity > 0.8
    assert 1 not in assignments


def test_enrichment_assigns_color_clusters_and_ocr_numbers(tmp_path):
    first_frame = _make_frame(tmp_path / "000001.jpg", (220, 30, 30))
    second_frame = _make_frame(tmp_path / "000002.jpg", (30, 30, 220))
    detections = [
        SimpleNamespace(
            frame_timestamp=0,
            bounding_box={"x": 20, "y": 10, "width": 40, "height": 80},
            dominant_rgb=None,
            team_color_cluster=None,
            jersey_number=None,
            jersey_number_confidence=None,
        ),
        SimpleNamespace(
            frame_timestamp=0.033,
            bounding_box={"x": 20, "y": 10, "width": 40, "height": 80},
            dominant_rgb=None,
            team_color_cluster=None,
            jersey_number=None,
            jersey_number_confidence=None,
        ),
    ]
    video = SimpleNamespace(file_path="test.mp4", status="analyzed", match=SimpleNamespace(status="analyzed"))
    session = FakeSession(video, detections)

    run_enrichment(
        1,
        ocr_reader=FakeOcrReader(),
        session_factory=lambda: session,
        frame_extractor=lambda _path, _directory: [first_frame, second_frame],
    )

    assert all(detection.dominant_rgb for detection in detections)
    assert {detection.team_color_cluster for detection in detections} == {0, 1}
    assert [detection.jersey_number for detection in detections] == [12, 12]
    assert all(detection.jersey_number_confidence == 0.88 for detection in detections)
    assert video.status == "analyzed"


class FakeSession:
    def __init__(self, video, detections):
        self.video = video
        self.detections = detections

    def get(self, _model, _video_id):
        return self.video

    def scalars(self, _query):
        return SimpleNamespace(all=lambda: self.detections)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class FakeOcrReader:
    def readtext(self, _crop, detail=1, paragraph=False):
        return [([], "12", 0.88)]


def _make_frame(path: Path, color: tuple[int, int, int]) -> Path:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:, :] = color
    Image.fromarray(image).save(path)
    return path