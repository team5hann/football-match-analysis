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
from app.models.player_stats import MatchPlayerStats
from app.models.video import Video
from app.services.pitch import pitch_xy
from app.services.player_identity import link_map, merge_player_identities

POSSESSION_DISTANCE = 0.2
PITCH_LENGTH_METERS = 105.0
PITCH_WIDTH_METERS = 68.0

# A "possession spell" is only real once the same player has been the
# nearest-to-ball for this many consecutive sampled frames. Without this the
# nearest-player-to-ball flickers between players every frame and every flicker
# was logged as a pass, producing tens of "passes" per second of quiet play.
POSSESSION_CONFIRM_FRAMES = 6
# A pass/turnover is only logged if the ball actually moved this far (metres)
# between the two spell owners - filters out tracker id-swaps on one player.
MIN_POSSESSION_CHANGE_METERS = 6.0


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


def analyze_records(
    records: list[dict],
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
    video_id: int | None = None,
    possession_confirm_frames: int = POSSESSION_CONFIRM_FRAMES,
    min_change_meters: float = MIN_POSSESSION_CHANGE_METERS,
) -> AnalysisResult:
    cluster_roles = cluster_roles or {}
    players_by_time: dict[float, list[dict]] = defaultdict(list)
    balls_by_time: dict[float, list[dict]] = defaultdict(list)
    # track_id now comes from ByteTrack (assigned at detection time); analysis
    # just consumes it. Player detections ByteTrack never bound to a track are
    # dropped - they cannot take part in tracking-based metrics.
    track_ids: dict[int, int] = {}
    for record in records:
        if record["class"] == "player":
            if record.get("track_id") is None:
                continue
            players_by_time[record["timestamp"]].append(record)
            if record.get("id") is not None:
                track_ids[record["id"]] = record["track_id"]
        else:
            balls_by_time[record["timestamp"]].append(record)

    ordered_times = sorted(players_by_time)

    owner_by_time: dict[float, dict | None] = {}
    touch_counts: dict[int, int] = defaultdict(int)
    distances: dict[int, float] = defaultdict(float)
    speeds: dict[int, list[float]] = defaultdict(list)
    last_position: dict[int, tuple[float, float, float]] = {}
    for timestamp in ordered_times:
        players = players_by_time[timestamp]
        balls = balls_by_time.get(timestamp, [])
        for player in players:
            # Movement distance/speed use real pitch metres (homography when
            # available, else the old image-linear estimate); possession below
            # stays in normalised units so its 0..1 threshold keeps working.
            metres_x, metres_y = pitch_xy(player, image_width, image_height)
            track_id = player["track_id"]
            if track_id in last_position:
                old_timestamp, old_x, old_y = last_position[track_id]
                elapsed = max(timestamp - old_timestamp, 0.001)
                distance = hypot(metres_x - old_x, metres_y - old_y)
                distances[track_id] += distance
                speeds[track_id].append(distance / elapsed)
            last_position[track_id] = (timestamp, metres_x, metres_y)
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

    # --- possession spells (debounced) -----------------------------------------
    # Collapse the per-frame owner sequence into spells; only a spell that held
    # for POSSESSION_CONFIRM_FRAMES consecutive frames counts as real possession.
    spells: list[dict] = []
    current_spell: dict | None = None
    for timestamp in ordered_times:
        owner = owner_by_time[timestamp]
        track_id = owner["track_id"] if owner else None
        if current_spell is not None and track_id == current_spell["track_id"]:
            current_spell["frames"] += 1
            current_spell["last_owner"] = owner
            current_spell["end_ts"] = timestamp
        else:
            if current_spell is not None and current_spell["frames"] >= possession_confirm_frames:
                spells.append(current_spell)
            current_spell = (
                {
                    "track_id": track_id,
                    "cluster": owner.get("cluster"),
                    "first_owner": owner,
                    "last_owner": owner,
                    "start_ts": timestamp,
                    "end_ts": timestamp,
                    "frames": 1,
                }
                if owner is not None
                else None
            )
    if current_spell is not None and current_spell["frames"] >= possession_confirm_frames:
        spells.append(current_spell)

    events: list[dict] = []
    for previous_spell, next_spell in zip(spells, spells[1:]):
        if previous_spell["track_id"] == next_spell["track_id"]:
            continue
        from_x, from_y = pitch_xy(previous_spell["last_owner"], image_width, image_height)
        to_x, to_y = pitch_xy(next_spell["first_owner"], image_width, image_height)
        if hypot(to_x - from_x, to_y - from_y) < min_change_meters:
            continue  # same spot, tracker just swapped the id
        same_cluster = (
            previous_spell["cluster"] is not None
            and previous_spell["cluster"] == next_spell["cluster"]
        )
        events.append(
            {
                "event_type": "pass" if same_cluster else "possession_loss",
                "timestamp_seconds": next_spell["start_ts"],
                "track_id": next_spell["track_id"],       # recipient / new owner
                "sender_track_id": previous_spell["track_id"],
                "video_id": video_id,
                "description": "Estimated from a debounced nearest-player possession change",
            }
        )

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
                "pitch": (item.pitch_x, item.pitch_y),
                # ByteTrack assigns this at detection time; analyze_records keys
                # all tracking metrics off it, so it must be carried through.
                "track_id": item.track_id,
            }
            for item in detections
        ]
        result = analyze_records(records, video.width or 1, video.height or 1, cluster_roles, video.id)
        # track_id already lives on each Detection (written by ByteTrack in the
        # detection phase); analysis no longer re-assigns it.

        # Stitch the ByteTrack track_ids into whole-match player identities, then tag
        # each per-track metric row with the identity it belongs to so callers
        # can aggregate touches/distance/etc. across a player's split tracks.
        merge_player_identities(match_id, db=db)
        match_player_by_track = link_map(db, match_id)

        # Re-analysis invalidates every derived event and the advanced stats
        # built from them; player-stats must be recomputed afterwards.
        db.execute(
            delete(Event).where(
                Event.match_id == match_id,
                Event.event_type.in_(["pass", "possession_loss", "duel", "dribble"]),
            )
        )
        db.execute(delete(MatchPlayerStats).where(MatchPlayerStats.match_id == match_id))
        db.execute(delete(PlayerMetric).where(PlayerMetric.match_id == match_id))
        for row in result.players:
            db.add(PlayerMetric(match_id=match_id, match_player_id=match_player_by_track.get(row["track_id"]), **row))
        for event in result.events:
            db.add(Event(
                match_id=match_id,
                event_type=event["event_type"],
                timestamp_seconds=event["timestamp_seconds"],
                track_id=event["track_id"],
                related_track_id=event.get("sender_track_id"),  # pass/turnover origin
                video_id=event["video_id"],
                description=event["description"],
                manually_verified=False,
            ))
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