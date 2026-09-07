from collections import Counter, defaultdict
from math import hypot

from app.services.pitch import pitch_norm


POSSESSION_DISTANCE = 0.2


def _center(record: dict, width: int, height: int) -> tuple[float, float]:
    # Pitch-normalised (0..1): real homography projection when available for the
    # detection, otherwise box centre over image size (old behaviour).
    return pitch_norm(record, width, height)


def _mode(values: list[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return Counter(known).most_common(1)[0][0] if known else None


def _possession_by_time(records: list[dict], width: int, height: int) -> dict[float, int | None]:
    """Fallback only: last-resort nearest-player-to-ball owner per timestamp,
    used when a pass event has no recorded sender."""
    players = defaultdict(list)
    balls = defaultdict(list)
    for record in records:
        (players if record["class"] == "player" else balls)[record["timestamp"]].append(record)

    ownership = {}
    for timestamp, ball_records in balls.items():
        if not players[timestamp] or not ball_records:
            ownership[timestamp] = None
            continue
        ball_x, ball_y = _center(ball_records[0], width, height)
        closest = min(
            players[timestamp],
            key=lambda player: hypot(*(a - b for a, b in zip(_center(player, width, height), (ball_x, ball_y)))),
        )
        player_x, player_y = _center(closest, width, height)
        ownership[timestamp] = (
            closest["track_id"] if hypot(player_x - ball_x, player_y - ball_y) <= POSSESSION_DISTANCE else None
        )
    return ownership


def build_passing_network(
    records: list[dict],
    events: list[dict],
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
    match_player_by_track: dict[int, int] | None = None,
) -> dict[str, dict]:
    """Team passing graph.

    * Nodes are **whole-match player identities** (``match_player_id``), so one
      real player is one node - tracks that were never merged fall back to their
      raw ``track_id`` as the node key.
    * Edges use the **sender recorded on each pass event** (``sender_track_id``,
      from the debounced analysis' ``Event.related_track_id``); the internal
      nearest-player possession replay is only a fallback for events that lack it.
    """
    cluster_roles = cluster_roles or {}
    match_player_by_track = match_player_by_track or {}
    identity_ids = set(match_player_by_track.values())

    def player_key(track_id: int | None) -> int | None:
        if track_id is None:
            return None
        return match_player_by_track.get(track_id, track_id)

    by_player: dict[int, list[dict]] = defaultdict(list)
    track_to_player: dict[int, int] = {}
    for record in records:
        if record["class"] == "player" and record.get("track_id") is not None:
            key = player_key(record["track_id"])
            by_player[key].append(record)
            track_to_player[record["track_id"]] = key

    nodes: dict[int, dict] = {}
    for key, player_records in by_player.items():
        cluster = _mode([record.get("cluster") for record in player_records])
        role = cluster_roles.get(cluster, "unknown") if cluster is not None else "unknown"
        positions = [_center(record, image_width, image_height) for record in player_records]
        average_x = sum(position[0] for position in positions) / len(positions)
        average_y = sum(position[1] for position in positions) / len(positions)
        jersey_number = _mode([record.get("jersey_number") for record in player_records])
        nodes[key] = {
            "track_id": key,  # node key: match_player_id when merged, else the raw track_id
            "match_player_id": key if key in identity_ids else None,
            "team_role": role,
            "jersey_number": jersey_number,
            "label": f"#{jersey_number}" if jersey_number is not None else f"Unknown #{key}",
            "average_x": round(average_x, 4),
            "average_y": round(average_y, 4),
            "detections_count": len(player_records),
        }

    ownership = _possession_by_time(records, image_width, image_height)
    sorted_ownership = sorted(ownership.items())
    edge_counts: Counter[tuple[int, int]] = Counter()
    for event in sorted(events, key=lambda item: item["timestamp"]):
        recipient = track_to_player.get(event.get("track_id"))
        sender_track = event.get("sender_track_id")
        if sender_track is None:
            sender_track = next(
                (owner for timestamp, owner in reversed(sorted_ownership)
                 if timestamp < event["timestamp"] and owner is not None),
                None,
            )
        sender = track_to_player.get(sender_track) if sender_track is not None else None
        if sender is None or recipient is None or sender == recipient:
            continue
        if sender not in nodes or recipient not in nodes:
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
        result[role] = {
            "role": role,
            "nodes": sorted(role_nodes, key=lambda node: node["track_id"]),
            "edges": role_edges,
        }
    return result
