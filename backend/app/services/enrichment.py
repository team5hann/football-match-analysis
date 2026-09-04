import re
import tempfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.detection import Detection
from app.models.enums import MatchStatus, VideoStatus
from app.models.video import Video
from app.services.detection import extract_frames


def dominant_torso_rgb(image: np.ndarray, bounding_box: dict[str, float]) -> list[int] | None:
    height, width = image.shape[:2]
    x = max(0, int(bounding_box["x"]))
    y = max(0, int(bounding_box["y"]))
    box_width = max(1, int(bounding_box["width"]))
    box_height = max(1, int(bounding_box["height"]))
    x_end = min(width, x + box_width)
    y_end = min(height, y + box_height)
    if x >= x_end or y >= y_end:
        return None

    crop = image[y + int((y_end - y) * 0.15) : y + int((y_end - y) * 0.65), x:x_end]
    if crop.size == 0:
        return None
    pixels = crop.reshape(-1, 3).astype(np.float32)
    pixels = pixels[:: max(1, len(pixels) // 500)]
    return [int(round(value)) for value in pixels.mean(axis=0)]


def cluster_colors(colors: list[list[int]], max_clusters: int = 2) -> list[int]:
    if not colors:
        return []
    values = np.asarray(colors, dtype=np.float32)
    cluster_count = min(max_clusters, len(values))
    centers = values[np.linspace(0, len(values) - 1, cluster_count).astype(int)].copy()
    for _ in range(12):
        distances = ((values[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        labels = distances.argmin(axis=1)
        updated = np.array(
            [values[labels == index].mean(axis=0) if np.any(labels == index) else centers[index] for index in range(cluster_count)]
        )
        if np.allclose(updated, centers):
            break
        centers = updated
    return labels.astype(int).tolist()


def read_jersey_number(reader, crop: np.ndarray) -> tuple[int | None, float | None]:
    for result in reader.readtext(crop, detail=1, paragraph=False):
        if len(result) < 3:
            continue
        match = re.search(r"(?<!\d)([0-9]{1,2})(?!\d)", str(result[1]))
        if match is None:
            continue
        number = int(match.group(1))
        if 0 <= number <= 99:
            return number, round(float(result[2]), 4)
    return None, None


def _crop_player(image: np.ndarray, bounding_box: dict[str, float]) -> np.ndarray | None:
    height, width = image.shape[:2]
    x = max(0, int(bounding_box["x"]))
    y = max(0, int(bounding_box["y"]))
    x_end = min(width, x + max(1, int(bounding_box["width"])))
    y_end = min(height, y + max(1, int(bounding_box["height"])))
    return image[y:y_end, x:x_end] if x < x_end and y < y_end else None


def run_enrichment(
    video_id: int,
    ocr_reader=None,
    session_factory=SessionLocal,
    frame_extractor: Callable[[str, Path], list[Path]] = extract_frames,
) -> None:
    db: Session = session_factory()
    video = db.get(Video, video_id)
    if video is None:
        db.close()
        return

    try:
        player_detections = db.scalars(
            select(Detection)
            .where(Detection.video_id == video_id, Detection.class_name == "player")
            .order_by(Detection.frame_timestamp, Detection.id)
        ).all()
        if not player_detections:
            raise ValueError("No player detections found; run detection first")

        video.status = VideoStatus.PROCESSING
        video.match.status = MatchStatus.PROCESSING
        db.commit()

        if ocr_reader is None:
            import easyocr

            ocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)

        with tempfile.TemporaryDirectory(prefix="football-enrichment-") as temp_dir:
            frames = frame_extractor(video.file_path, Path(temp_dir))
            frame_by_timestamp: dict[int, np.ndarray] = {}
            for index, frame_path in enumerate(frames):
                with Image.open(frame_path) as frame:
                    frame_by_timestamp[index] = np.asarray(frame.convert("RGB"))
            colors: list[list[int]] = []
            enriched: list[Detection] = []
            for detection in player_detections:
                image = frame_by_timestamp.get(int(detection.frame_timestamp))
                if image is None:
                    continue
                color = dominant_torso_rgb(image, detection.bounding_box)
                crop = _crop_player(image, detection.bounding_box)
                if color is None or crop is None:
                    continue
                detection.dominant_rgb = color
                colors.append(color)
                enriched.append(detection)
                number, confidence = read_jersey_number(ocr_reader, crop)
                detection.jersey_number = number
                detection.jersey_number_confidence = confidence

            labels = cluster_colors(colors)
            for detection, label in zip(enriched, labels):
                detection.team_color_cluster = label

        video.status = VideoStatus.ANALYZED
        video.match.status = MatchStatus.ANALYZED
        db.commit()
    except Exception as exc:
        db.rollback()
        video.status = VideoStatus.FAILED
        video.error_message = f"Enrichment failed: {exc}"
        video.match.status = MatchStatus.FAILED
        db.commit()
    finally:
        db.close()