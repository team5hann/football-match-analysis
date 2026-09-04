from collections import Counter, defaultdict
from dataclasses import dataclass
from math import hypot


@dataclass
class TacticalResult:
    team_role: str
    formation: str
    width: float
    depth: float
    compactness: float
    players: list[dict]
    coordinate_note: str


def _center(box: dict[str, float], image_width: int, image_height: int) -> tuple[float, float]:
    return (
        (box["x"] + box["width"] / 2) / max(image_width, 1),
        (box["y"] + box["height"] / 2) / max(image_height, 1),
    )


def _mode(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return Counter(known).most_common(1)[0][0] if known else None


def _formation(y_positions: list[float]) -> str:
    if not y_positions:
        return "Unknown"
    if len(y_positions) < 3:
        return str(len(y_positions))

    values = y_positions[:]
    centers = [min(values), sum(values) / len(values), max(values)]
    for _ in range(10):
        groups = [[] for _ in centers]
        for value in values:
            group = min(range(len(centers)), key=lambda index: abs(value - centers[index]))
            groups[group].append(value)
        updated = [sum(group) / len(group) if group else centers[index] for index, group in enumerate(groups)]
        if updated == centers:
            break
        centers = updated
    counts = sorted((len(group) for group in groups if group), reverse=True)
    return "-".join(str(count) for count in counts)


def calculate_tactical(
    records: list[dict],
    team_role: str,
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
) -> TacticalResult:
    cluster_roles = cluster_roles or {}
    team_records = [
        record
        for record in records
        if record.get("class") == "player"
        and cluster_roles.get(record.get("cluster")) == team_role
    ]
    by_track: dict[int, list[dict]] = defaultdict(list)
    by_timestamp: dict[float, list[dict]] = defaultdict(list)
    for record in team_records:
        by_track[record["track_id"]].append(record)
        by_timestamp[record["timestamp"]].append(record)

    players = []
    for track_id, player_records in sorted(by_track.items()):
        positions = [_center(record["box"], image_width, image_height) for record in player_records]
        average_x = sum(position[0] for position in positions) / len(positions)
        average_y = sum(position[1] for position in positions) / len(positions)
        jersey_number = _mode([record.get("jersey_number") for record in player_records])
        players.append(
            {
                "track_id": track_id,
                "jersey_number": jersey_number,
                "label": f"#{jersey_number}" if jersey_number is not None else f"Unknown #{track_id}",
                "average_x": round(average_x, 4),
                "average_y": round(average_y, 4),
                "detections_count": len(player_records),
            }
        )

    if not players:
        return TacticalResult(team_role, "Unknown", 0, 0, 0, [], _coordinate_note())

    width = max(player["average_x"] for player in players) - min(player["average_x"] for player in players)
    depth = max(player["average_y"] for player in players) - min(player["average_y"] for player in players)
    pair_distances = []
    for timestamp_players in by_timestamp.values():
        positions = [_center(record["box"], image_width, image_height) for record in timestamp_players]
        for index, first in enumerate(positions):
            pair_distances.extend(hypot(first[0] - second[0], first[1] - second[1]) for second in positions[index + 1 :])
    compactness = sum(pair_distances) / len(pair_distances) if pair_distances else 0
    return TacticalResult(
        team_role,
        _formation([player["average_y"] for player in players]),
        round(width, 4),
        round(depth, 4),
        round(compactness, 4),
        players,
        _coordinate_note(),
    )


def _coordinate_note() -> str:
    return "Approximate camera coordinates from detection box centers; not calibrated to the real pitch. Formation is a rough whole-video average, not time-varying."