from collections import Counter, defaultdict
from math import hypot


POSSESSION_DISTANCE = 0.2


def _center(box: dict[str, float], width: int, height: int) -> tuple[float, float]:
    return (
        (box["x"] + box["width"] / 2) / max(width, 1),
        (box["y"] + box["height"] / 2) / max(height, 1),
    )


def _mode(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return Counter(known).most_common(1)[0][0] if known else None


def _possession_by_time(records: list[dict], width: int, height: int) -> dict[float, int | None]:
    players = defaultdict(list)
    balls = defaultdict(list)
    for record in records:
        (players if record["class"] == "player" else balls)[record["timestamp"]].append(record)

    ownership = {}
    for timestamp, ball_records in balls.items():
        if not players[timestamp] or not ball_records:
            ownership[timestamp] = None
            continue
        ball_x, ball_y = _center(ball_records[0]["box"], width, height)
        closest = min(
            players[timestamp],
            key=lambda player: hypot(*(a - b for a, b in zip(_center(player["box"], width, height), (ball_x, ball_y)))),
        )
        player_x, player_y = _center(closest["box"], width, height)
        ownership[timestamp] = closest["track_id"] if hypot(player_x - ball_x, player_y - ball_y) <= POSSESSION_DISTANCE else None
    return ownership


def build_passing_network(
    records: list[dict],
    events: list[dict],
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
    match_player_by_track: dict[int, int] | None = None,
) -> dict[str, dict]:
    cluster_roles = cluster_roles or {}
    match_player_by_track = match_player_by_track or {}
    by_track: dict[int, list[dict]] = defaultdict(list)
    for record in records:
        if record["class"] == "player" and record.get("track_id") is not None:
            by_track[record["track_id"]].append(record)

    nodes: dict[int, dict] = {}
    for track_id, player_records in by_track.items():
        cluster = _mode([record.get("cluster") for record in player_records])
        role = cluster_roles.get(cluster, "unknown") if cluster is not None else "unknown"
        positions = [_center(record["box"], image_width, image_height) for record in player_records]
        average_x = sum(position[0] for position in positions) / len(positions)
        average_y = sum(position[1] for position in positions) / len(positions)
        jersey_number = _mode([record.get("jersey_number") for record in player_records])
        nodes[track_id] = {
            "track_id": track_id,
            # Whole-match identity this track belongs to, when available. The
            # graph is still built per track_id; callers may group nodes/edges
            # by this to show one node per stitched player.
            "match_player_id": match_player_by_track.get(track_id),
            "team_role": role,
            "jersey_number": jersey_number,
            "label": f"#{jersey_number}" if jersey_number is not None else f"Unknown #{track_id}",
            "average_x": round(average_x, 4),
            "average_y": round(average_y, 4),
            "detections_count": len(player_records),
        }

    ownership = _possession_by_time(records, image_width, image_height)
    sorted_ownership = sorted(ownership.items())
    edge_counts: Counter[tuple[int, int]] = Counter()
    for event in sorted(events, key=lambda item: item["timestamp"]):
        recipient = event.get("track_id")
        if recipient not in nodes:
            continue
        sender = next(
            (owner for timestamp, owner in reversed(sorted_ownership) if timestamp < event["timestamp"] and owner is not None),
            None,
        )
        if sender is None or sender == recipient or sender not in nodes:
            continue
        if nodes[sender]["team_role"] != nodes[recipient]["team_role"]:
            continue
        if nodes[sender]["team_role"] not in ("home", "away"):
            continue
        edge_counts[(sender, recipient)] += 1

    result = {}
    for role in ("home", "away"):
        role_nodes = [node for node in nodes.values() if node["team_role"] == role]
        role_ids = {node["track_id"] for node in role_nodes}
        role_edges = [
            {
                "source_track_id": source,
                "target_track_id": target,
                "pass_count": count,
                "source_x": nodes[source]["average_x"],
                "source_y": nodes[source]["average_y"],
                "target_x": nodes[target]["average_x"],
                "target_y": nodes[target]["average_y"],
            }
            for (source, target), count in sorted(edge_counts.items())
            if source in role_ids and target in role_ids
        ]
        result[role] = {"role": role, "nodes": sorted(role_nodes, key=lambda node: node["track_id"]), "edges": role_edges}
    return result