from collections import defaultdict
from dataclasses import dataclass
from math import hypot

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.analysis import MatchAnalysisSummary, PlayerMetric
from app.models.detection import Detection
from app.models.enums import MatchStatus, VideoStatus
from app.models.event import Event
from app.models.match import Match
from app.models.video import Video
from app.services.player_identity import link_map, merge_player_identities

IOU_THRESHOLD = 0.15
POSSESSION_DISTANCE = 0.2
PITCH_LENGTH_METERS = 105.0
PITCH_WIDTH_METERS = 68.0


@dataclass
class AnalysisResult:
    track_ids: dict[int, int]
    home_possession_pct: float
    away_possession_pct: float
    players: list[dict]
    events: list[dict]


def box_center(box: dict[str, float], image_width: int, image_height: int) -> tuple[float, float]:
    return (
        (box["x"] + box["width"] / 2) / max(image_width, 1),
        (box["y"] + box["height"] / 2) / max(image_height, 1),
    )


def iou(first: dict[str, float], second: dict[str, float]) -> float:
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union else 0.0


def analyze_records(
    records: list[dict],
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
    video_id: int | None = None,
) -> AnalysisResult:
    cluster_roles = cluster_roles or {}
    players_by_time: dict[float, list[dict]] = defaultdict(list)
    balls_by_time: dict[float, list[dict]] = defaultdict(list)
    for record in records:
        target = players_by_time if record["class"] == "player" else balls_by_time
        target[record["timestamp"]].append(record)

    ordered_times = sorted(players_by_time)
    next_track_id = 1
    previous: list[dict] = []
    track_ids: dict[int, int] = {}
    for timestamp in ordered_times:
        current = players_by_time[timestamp]
        candidates: list[tuple[float, int, int]] = []
        for current_index, item in enumerate(current):
            for previous_index, old_item in enumerate(previous):
                score = iou(item["box"], old_item["box"])
                if item.get("cluster") is not None and old_item.get("cluster") == item["cluster"]:
                    score += 0.1
                if score >= IOU_THRESHOLD:
                    candidates.append((score, current_index, previous_index))
        matched_current: set[int] = set()
        matched_previous: set[int] = set()
        for _, current_index, previous_index in sorted(candidates, reverse=True):
            if current_index in matched_current or previous_index in matched_previous:
                continue
            track_ids[current[current_index]["id"]] = previous[previous_index]["track_id"]
            current[current_index]["track_id"] = previous[previous_index]["track_id"]
            matched_current.add(current_index)
            matched_previous.add(previous_index)
        for current_index, item in enumerate(current):
            if current_index not in matched_current:
                item["track_id"] = next_track_id
                track_ids[item["id"]] = next_track_id
                next_track_id += 1
        previous = current

    owner_by_time: dict[float, dict | None] = {}
    touch_counts: dict[int, int] = defaultdict(int)
    distances: dict[int, float] = defaultdict(float)
    speeds: dict[int, list[float]] = defaultdict(list)
    last_position: dict[int, tuple[float, float, float]] = {}
    for timestamp in ordered_times:
        players = players_by_time[timestamp]
        balls = balls_by_time.get(timestamp, [])
        for player in players:
            player_x, player_y = box_center(player["box"], image_width, image_height)
            track_id = player["track_id"]
            if track_id in last_position:
                old_timestamp, old_x, old_y = last_position[track_id]
                elapsed = max(timestamp - old_timestamp, 0.001)
                distance = hypot(
                    (player_x - old_x) * PITCH_LENGTH_METERS,
                    (player_y - old_y) * PITCH_WIDTH_METERS,
                )
                distances[track_id] += distance
                speeds[track_id].append(distance / elapsed)
            last_position[track_id] = (timestamp, player_x, player_y)
        if not balls:
            owner_by_time[timestamp] = None
            continue
        ball_x, ball_y = box_center(balls[0]["box"], image_width, image_height)
        closest = min(
            players,
            key=lambda item: hypot(*(a - b for a, b in zip(box_center(item["box"], image_width, image_height), (ball_x, ball_y)))),
            default=None,
        )
        if closest is None:
            owner_by_time[timestamp] = None
            continue
        player_x, player_y = box_center(closest["box"], image_width, image_height)
        distance_to_ball = hypot(player_x - ball_x, player_y - ball_y)
        owner_by_time[timestamp] = closest if distance_to_ball <= POSSESSION_DISTANCE else None
        if distance_to_ball <= POSSESSION_DISTANCE:
            touch_counts[closest["track_id"]] += 1

    events: list[dict] = []
    previous_owner: dict | None = None
    for timestamp in ordered_times:
        owner = owner_by_time[timestamp]
        if owner and previous_owner and owner["track_id"] != previous_owner["track_id"]:
            same_cluster = (
                owner.get("cluster") is not None
                and owner.get("cluster") == previous_owner.get("cluster")
            )
            events.append(
                {
                    "event_type": "pass" if same_cluster else "possession_loss",
                    "timestamp_seconds": timestamp,
                    "track_id": owner["track_id"],
                    "video_id": video_id,
                    "description": "Estimated from nearest-player possession change",
                }
            )
        if owner:
            previous_owner = owner

    # TODO: shot detection needs goal/pitch geometry; do not infer it from sparse detections.

    player_rows = []
    all_players = {item["track_id"]: item for items in players_by_time.values() for item in items}
    for track_id, item in sorted(all_players.items()):
        track_speeds = speeds[track_id]
        player_rows.append(
            {
                "track_id": track_id,
                "team_color_cluster": item.get("cluster"),
                "jersey_number": item.get("jersey_number"),
                "touches": touch_counts[track_id],
                "distance_meters": round(distances[track_id], 2),
                "average_speed_mps": round(sum(track_speeds) / len(track_speeds), 2) if track_speeds else 0,
                "max_speed_mps": round(max(track_speeds), 2) if track_speeds else 0,
            }
        )

    possession = {"home": 0, "away": 0}
    for owner in owner_by_time.values():
        if owner and cluster_roles.get(owner.get("cluster")) in possession:
            possession[cluster_roles[owner["cluster"]]] += 1
    total_possession = sum(possession.values())
    home_pct = round(possession["home"] / total_possession * 100, 2) if total_possession else 0
    return AnalysisResult(track_ids, home_pct, round(100 - home_pct, 2) if total_possession else 0, player_rows, events)


def run_analysis(match_id: int, session_factory=SessionLocal) -> None:
    db: Session = session_factory()
    match = db.get(Match, match_id)
    if match is None:
        db.close()
        return
    try:
        videos = db.scalars(select(Video).where(Video.match_id == match_id)).all()
        if not videos:
            raise ValueError("No video found")
        video = videos[0]
        detections = db.scalars(select(Detection).where(Detection.video_id == video.id).order_by(Detection.frame_timestamp, Detection.id)).all()
        if not detections:
            raise ValueError("Run detection before analysis")
        cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
        records = [
            {
                "id": item.id,
                "timestamp": item.frame_timestamp,
                "class": item.class_name,
                "box": item.bounding_box,
                "cluster": item.team_color_cluster,
                "jersey_number": item.jersey_number,
            }
            for item in detections
        ]
        result = analyze_records(records, video.width or 1, video.height or 1, cluster_roles, video.id)
        for item in detections:
            if item.id in result.track_ids:
                item.track_id = result.track_ids[item.id]
        db.flush()  # persist track_ids so the identity merge sees them

        # Stitch the fresh track_ids into whole-match player identities, then tag
        # each per-track metric row with the identity it belongs to so callers
        # can aggregate touches/distance/etc. across a player's split tracks.
        merge_player_identities(match_id, db=db)
        match_player_by_track = link_map(db, match_id)

        db.execute(delete(Event).where(Event.match_id == match_id, Event.event_type.in_(["pass", "possession_loss"])))
        db.execute(delete(PlayerMetric).where(PlayerMetric.match_id == match_id))
        for row in result.players:
            db.add(PlayerMetric(match_id=match_id, match_player_id=match_player_by_track.get(row["track_id"]), **row))
        for event in result.events:
            db.add(Event(match_id=match_id, event_type=event["event_type"], timestamp_seconds=event["timestamp_seconds"], track_id=event["track_id"], video_id=event["video_id"], description=event["description"], manually_verified=False))
        summary = db.scalar(select(MatchAnalysisSummary).where(MatchAnalysisSummary.match_id == match_id))
        if summary is None:
            summary = MatchAnalysisSummary(match_id=match_id)
            db.add(summary)
        summary.home_possession_pct = result.home_possession_pct
        summary.away_possession_pct = result.away_possession_pct
        video.status = VideoStatus.ANALYZED
        match.status = MatchStatus.ANALYZED
        db.commit()
    except Exception as exc:
        db.rollback()
        match.status = MatchStatus.FAILED
        if videos:
            videos[0].status = VideoStatus.FAILED
            videos[0].error_message = f"Analysis failed: {exc}"
        db.commit()
    finally:
        db.close()