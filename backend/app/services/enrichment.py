import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from math import sqrt
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import get_settings
from app.models.detection import Detection
from app.models.enums import MatchStatus, VideoStatus
from app.models.team_cluster import TeamClusterAssignment
from app.models.video import Video
from app.services.detection import extract_frames

settings = get_settings()
COLOR_SIMILARITY_THRESHOLD = 0.70
COLOR_AMBIGUITY_MARGIN = 0.12


@dataclass(frozen=True)
class AutomaticClusterAssignment:
    role: str
    similarity: float


def assign_cluster_roles(
    cluster_colors_by_id: dict[int, list[float]],
    home_color: str | None,
    away_color: str | None,
) -> dict[int, AutomaticClusterAssignment]:
    """Assign only clusters with a clear, sufficiently close kit-color match."""
    targets = {"home": _hex_to_rgb(home_color), "away": _hex_to_rgb(away_color)}
    targets = {role: color for role, color in targets.items() if color is not None}
    if not targets:
        return {}

    candidates: dict[int, AutomaticClusterAssignment] = {}
    for cluster_id, cluster_color in cluster_colors_by_id.items():
        ranked = sorted(
            (
                (similarity, role)
                for role, target in targets.items()
                if (similarity := _rgb_similarity(cluster_color, target)) is not None
            ),
            reverse=True,
        )
        if not ranked:
            continue
        best_similarity, best_role = ranked[0]
        second_similarity = ranked[1][0] if len(ranked) > 1 else 0.0
        if best_similarity >= COLOR_SIMILARITY_THRESHOLD and best_similarity - second_similarity >= COLOR_AMBIGUITY_MARGIN:
            candidates[cluster_id] = AutomaticClusterAssignment(best_role, round(best_similarity, 4))

    # More than one similarly close cluster for one team is not safe to resolve automatically.
    for role in targets:
        same_role = sorted(
            ((cluster_id, assignment) for cluster_id, assignment in candidates.items() if assignment.role == role),
            key=lambda item: item[1].similarity,
            reverse=True,
        )
        if len(same_role) > 1 and same_role[0][1].similarity - same_role[1][1].similarity < COLOR_AMBIGUITY_MARGIN:
            for cluster_id, _ in same_role:
                candidates.pop(cluster_id, None)
        elif len(same_role) > 1:
            for cluster_id, _ in same_role[1:]:
                candidates.pop(cluster_id, None)
    return candidates


def _hex_to_rgb(value: str | None) -> tuple[float, float, float] | None:
    if value is None or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return None
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def _rgb_similarity(first: list[float], second: tuple[float, float, float] | None) -> float | None:
    if second is None or len(first) != 3:
        return None
    distance = sqrt(sum((float(value) - target) ** 2 for value, target in zip(first, second)))
    return max(0.0, 1.0 - distance / 441.673)


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
                frame_index = round(detection.frame_timestamp / settings.detection_sample_interval_seconds)
                image = frame_by_timestamp.get(frame_index)
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

            cluster_colors_by_id = {
                cluster_id: [
                    float(np.mean([color[channel] for color, item_label in zip(colors, labels) if item_label == cluster_id]))
                    for channel in range(3)
                ]
                for cluster_id in set(labels)
            }
            automatic_assignments = assign_cluster_roles(
                cluster_colors_by_id,
                video.match.home_team_color,
                video.match.away_team_color,
            )
            if video.match.home_team_color or video.match.away_team_color:
                _save_automatic_assignments(db, video, automatic_assignments)

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


def _save_automatic_assignments(
    db: Session, video: Video, assignments: dict[int, AutomaticClusterAssignment]
) -> None:
    existing = {
        item.cluster_id: item
        for item in db.scalars(
            select(TeamClusterAssignment).where(TeamClusterAssignment.match_id == video.match_id)
        ).all()
    }
    for assignment in existing.values():
        if assignment.assignment_source == "automatic":
            db.delete(assignment)
    for cluster_id, automatic in assignments.items():
        if cluster_id in existing and existing[cluster_id].assignment_source == "manual":
            continue
        db.add(
            TeamClusterAssignment(
                match_id=video.match_id,
                cluster_id=cluster_id,
                role=automatic.role,
                team_id=(video.match.home_team_id if automatic.role == "home" else video.match.away_team_id),
                assignment_source="automatic",
                similarity=automatic.similarity,
            )
        )