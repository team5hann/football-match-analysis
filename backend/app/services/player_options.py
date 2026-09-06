from collections import Counter


def mode_or_none(values: list[int | None]) -> int | None:
    known_values = [value for value in values if value is not None]
    return Counter(known_values).most_common(1)[0][0] if known_values else None


def build_player_options(
    records: list[dict],
    cluster_roles: dict[int, str] | None = None,
    match_player_by_track: dict[int, int] | None = None,
) -> list[dict]:
    cluster_roles = cluster_roles or {}
    match_player_by_track = match_player_by_track or {}
    grouped: dict[int, list[dict]] = {}
    for record in records:
        track_id = record.get("track_id")
        if record.get("class") == "player" and track_id is not None:
            grouped.setdefault(track_id, []).append(record)

    options = []
    for track_id, player_records in sorted(grouped.items()):
        cluster = mode_or_none([record.get("team_color_cluster") for record in player_records])
        jersey_number = mode_or_none([record.get("jersey_number") for record in player_records])
        role = cluster_roles.get(cluster, "unknown") if cluster is not None else "unknown"
        number_label = f"#{jersey_number}" if jersey_number is not None else f"Unknown #{track_id}"
        options.append(
            {
                "track_id": track_id,
                # Whole-match identity this track was merged into, when the
                # identity layer has been built for the match (else None).
                "match_player_id": match_player_by_track.get(track_id),
                "team_color_cluster": cluster,
                "team_role": role,
                "jersey_number": jersey_number,
                "label": f"{role.title()} · {number_label}",
                "detection_count": len(player_records),
            }
        )
    return options