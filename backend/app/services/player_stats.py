"""Advanced per-player stats: passes, shots, duels, dribbles.

Layers on top of what already exists:
* Phase 4 ``pass`` / ``possession_loss`` events,
* Phase 5d ``shot`` events (already carry the shooter ``track_id``),
* Phase 5c/5d standardised 105x68 m pitch coordinates,
* the whole-match ``match_player`` identities (last step).

TRANSPARENCY / ACCURACY WARNING
--------------------------------
Passes and shots reuse existing heuristic events, so they inherit that noise.
**Duels and dribbles are entirely new, unvalidated heuristics** built only from
sparse box centres and nearest-player-to-ball possession. There is no ground
truth behind them. A "duel" here is just "two opponents near each other and the
ball, then one team has it next"; it may really have been a loose ball, a
shoulder-to-shoulder run, or nothing. Treat duel/dribble counts as an
experimental signal, not a reliable metric.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from math import hypot

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.services.pitch import pitch_norm, pitch_xy
from app.models.analysis import PlayerMetric
from app.models.detection import Detection
from app.models.event import Event
from app.models.match import Match
from app.models.player_identity import MatchPlayer, TrackPlayerLink
from app.models.player_stats import MatchPlayerStats
from app.models.video import Video

# Standardised pitch, matches shots.py / Phase 5c-5d.
PITCH_LENGTH_METERS = 105.0
PITCH_WIDTH_METERS = 68.0

# --- heuristic thresholds (all deliberately simple, tune here) ---------------
# A completed/attempted pass under this sender->receiver distance is "short".
SHORT_PASS_MAX_METERS = 15.0
# Normalised nearest-player-to-ball distance that counts as having possession
# (same value analysis.py / passing_network.py use).
POSSESSION_DISTANCE = 0.2
# Two opponents closer than this (in metres) are contesting the same space.
DUEL_PROXIMITY_METERS = 2.0
# ... and both must be at least this close to the ball for it to be a duel.
DUEL_BALL_DISTANCE_METERS = 3.0
# A dribble must span at least this many consecutive possession samples,
DRIBBLE_MIN_FRAMES = 3
# cover at least this ball-carrier travel distance,
DRIBBLE_MIN_DISTANCE_METERS = 3.0
# and have an opponent within this distance at some point during the run.
DRIBBLE_OPPONENT_METERS = 5.0


@dataclass
class _PlayerStatRow:
    track_id: int
    passes_total: int = 0
    passes_completed: int = 0
    passes_short: int = 0
    passes_long: int = 0
    shots: int = 0
    xg: float = 0.0
    duels_total: int = 0
    duels_won: int = 0
    dribbles_total: int = 0
    dribbles_completed: int = 0

    def as_dict(self) -> dict:
        row = self.__dict__.copy()
        row["xg"] = round(row["xg"], 4)
        return row


@dataclass
class AdvancedStatsResult:
    player_rows: list[dict] = field(default_factory=list)          # per track_id
    duel_events: list[dict] = field(default_factory=list)
    dribble_events: list[dict] = field(default_factory=list)


def _as_record(record_or_box: dict) -> dict:
    """Accept either a full detection record (with 'box') or a bare box dict."""
    return record_or_box if "box" in record_or_box else {"box": record_or_box}


def _pitch(record_or_box: dict, image_width: int, image_height: int) -> tuple[float, float]:
    """Pitch metres - real homography position when the record carries one,
    otherwise the old image-linear estimate."""
    return pitch_xy(_as_record(record_or_box), image_width, image_height)


def _norm_center(record_or_box: dict, image_width: int, image_height: int) -> tuple[float, float]:
    return pitch_norm(_as_record(record_or_box), image_width, image_height)


def compute_advanced_stats(
    records: list[dict],
    pass_events: list[dict],
    shot_events: list[dict],
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
) -> AdvancedStatsResult:
    """Pure computation. ``records`` are detection dicts with keys
    class/track_id/timestamp/box/cluster; ``pass_events`` are the stored
    ``pass``/``possession_loss`` events (dicts with event_type/timestamp/
    track_id); ``shot_events`` the stored ``shot`` events (timestamp/track_id/xg).
    """
    cluster_roles = cluster_roles or {}
    rows: dict[int, _PlayerStatRow] = {}

    def row(track_id: int | None) -> _PlayerStatRow | None:
        if track_id is None:
            return None
        return rows.setdefault(track_id, _PlayerStatRow(track_id))

    def role_of(record: dict | None) -> str:
        if record is None:
            return "unknown"
        return cluster_roles.get(record.get("cluster"), "unknown")

    players_by_time: dict[float, list[dict]] = defaultdict(list)
    balls_by_time: dict[float, list[dict]] = defaultdict(list)
    for record in records:
        (players_by_time if record.get("class") == "player" else balls_by_time)[record["timestamp"]].append(record)
    timestamps = sorted(set(players_by_time) | set(balls_by_time))

    # --- possession owner per sampled timestamp -----------------------------
    owner_by_time: dict[float, dict | None] = {}
    for timestamp in timestamps:
        players = players_by_time.get(timestamp, [])
        balls = balls_by_time.get(timestamp, [])
        if not players or not balls:
            owner_by_time[timestamp] = None
            continue
        ball_x, ball_y = _norm_center(balls[0]["box"], image_width, image_height)
        nearest = min(
            players,
            key=lambda player: hypot(
                *(a - b for a, b in zip(_norm_center(player["box"], image_width, image_height), (ball_x, ball_y)))
            ),
        )
        near_x, near_y = _norm_center(nearest["box"], image_width, image_height)
        owner_by_time[timestamp] = nearest if hypot(near_x - ball_x, near_y - ball_y) <= POSSESSION_DISTANCE else None

    def last_owner_before(t: float) -> dict | None:
        prior = [ts for ts in timestamps if ts < t and owner_by_time.get(ts) is not None]
        return owner_by_time[prior[-1]] if prior else None

    def player_at(track_id: int, timestamp: float) -> dict | None:
        for player in players_by_time.get(timestamp, []):
            if player.get("track_id") == track_id:
                return player
        return None

    # --- 1) passes --------------------------------------------------------------
    for event in sorted(pass_events, key=lambda item: item["timestamp"]):
        timestamp = event["timestamp"]
        recipient_track = event.get("track_id")
        sender = last_owner_before(timestamp)
        if sender is None:
            continue
        sender_row = row(sender.get("track_id"))
        if sender_row is None:
            continue

        # distance sender -> the player who ended up with the ball
        recipient_box = None
        recipient = player_at(recipient_track, timestamp) if recipient_track is not None else None
        if recipient is not None:
            recipient_box = recipient["box"]
        if recipient_box is None:
            # fall back to the ball position at the event time
            balls = balls_by_time.get(timestamp, [])
            recipient_box = balls[0]["box"] if balls else sender["box"]
        sx, sy = _pitch(sender["box"], image_width, image_height)
        rx, ry = _pitch(recipient_box, image_width, image_height)
        distance = hypot(rx - sx, ry - sy)

        sender_row.passes_total += 1
        if distance < SHORT_PASS_MAX_METERS:
            sender_row.passes_short += 1
        else:
            sender_row.passes_long += 1
        # A "pass" event means possession stayed with the same team -> completed.
        # A "possession_loss" is booked as a FAILED pass for the player who had
        # the ball. This deliberately over-simplifies: the turnover may really
        # have been a tackle, foul or miscontrol rather than a bad pass, but
        # without dedicated event detection this is the closest approximation.
        if event.get("event_type") == "pass":
            sender_row.passes_completed += 1

    # --- 2) shots ------------------------------------------------------------
    for event in shot_events:
        shooter_row = row(event.get("track_id"))
        if shooter_row is None:
            continue
        shooter_row.shots += 1
        shooter_row.xg += float(event.get("xg") or 0.0)

    # --- 3) duels (NEW, rough) --------------------------------------------------
    result = AdvancedStatsResult()
    active_pair_run: dict[tuple[int, int], float] = {}  # pair -> first timestamp of current run
    for index, timestamp in enumerate(timestamps):
        players = players_by_time.get(timestamp, [])
        balls = balls_by_time.get(timestamp, [])
        contested_now: set[tuple[int, int]] = set()
        if players and balls:
            ball_x, ball_y = _pitch(balls[0]["box"], image_width, image_height)
            near_ball = []
            for player in players:
                if player.get("track_id") is None:
                    continue
                px, py = _pitch(player["box"], image_width, image_height)
                if hypot(px - ball_x, py - ball_y) <= DUEL_BALL_DISTANCE_METERS:
                    near_ball.append((player, (px, py)))
            for i in range(len(near_ball)):
                for j in range(i + 1, len(near_ball)):
                    first, (fx, fy) = near_ball[i]
                    second, (gx, gy) = near_ball[j]
                    if role_of(first) == role_of(second) or role_of(first) not in ("home", "away"):
                        continue
                    if role_of(second) not in ("home", "away"):
                        continue
                    if hypot(fx - gx, fy - gy) > DUEL_PROXIMITY_METERS:
                        continue
                    pair = tuple(sorted((first["track_id"], second["track_id"])))
                    contested_now.add(pair)
                    active_pair_run.setdefault(pair, timestamp)

        # resolve pairs whose contact run just ended (not contested this frame)
        next_owner = owner_by_time.get(timestamps[index + 1]) if index + 1 < len(timestamps) else None
        for pair in list(active_pair_run):
            if pair in contested_now:
                continue
            start_ts = active_pair_run.pop(pair)
            winner_role = role_of(next_owner)
            if winner_role not in ("home", "away"):
                continue  # nobody clearly won -> don't invent a duel result
            a, b = pair
            a_role = role_of(player_at(a, start_ts))
            if a_role == winner_role:
                winner_track, loser_track = a, b
            else:
                winner_track, loser_track = b, a
            row(winner_track).duels_total += 1
            row(winner_track).duels_won += 1
            row(loser_track).duels_total += 1
            result.duel_events.append(
                {
                    "timestamp": start_ts,
                    "winner_track_id": winner_track,
                    "loser_track_id": loser_track,
                    "description": "Rough duel estimate: opponents contesting the ball, next possession decided the winner",
                }
            )

    # --- 4) dribbles (NEW, rough) --------------------------------------------
    run_start = 0
    while run_start < len(timestamps):
        owner = owner_by_time.get(timestamps[run_start])
        owner_track = owner.get("track_id") if owner else None
        if owner_track is None:
            run_start += 1
            continue
        run_end = run_start
        while (
            run_end + 1 < len(timestamps)
            and (owner_by_time.get(timestamps[run_end + 1]) or {}).get("track_id") == owner_track
        ):
            run_end += 1
        run_length = run_end - run_start + 1
        if run_length >= DRIBBLE_MIN_FRAMES:
            positions = [
                _pitch(player_at(owner_track, timestamps[i])["box"], image_width, image_height)
                for i in range(run_start, run_end + 1)
                if player_at(owner_track, timestamps[i]) is not None
            ]
            travelled = sum(
                hypot(positions[k + 1][0] - positions[k][0], positions[k + 1][1] - positions[k][1])
                for k in range(len(positions) - 1)
            )
            carrier_role = role_of(owner)
            nearest_opponent: int | None = None
            opponent_seen = False
            for i in range(run_start, run_end + 1):
                carrier = player_at(owner_track, timestamps[i])
                if carrier is None:
                    continue
                cx, cy = _pitch(carrier["box"], image_width, image_height)
                for other in players_by_time.get(timestamps[i], []):
                    other_track = other.get("track_id")
                    if other_track is None or other_track == owner_track:
                        continue
                    if role_of(other) == carrier_role or role_of(other) not in ("home", "away"):
                        continue
                    ox, oy = _pitch(other["box"], image_width, image_height)
                    if hypot(ox - cx, oy - cy) <= DRIBBLE_OPPONENT_METERS:
                        opponent_seen = True
                        nearest_opponent = other["track_id"]
            if travelled > DRIBBLE_MIN_DISTANCE_METERS and opponent_seen:
                after = owner_by_time.get(timestamps[run_end + 1]) if run_end + 1 < len(timestamps) else None
                after_track = after.get("track_id") if after else None
                # "successful" if they still had it (or the ball simply went out
                # of tracking); "unsuccessful" if a different player - especially
                # an opponent - won it immediately after the run.
                lost = after_track is not None and after_track != owner_track
                outcome = "unsuccessful" if lost else "successful"
                dribbler_row = row(owner_track)
                dribbler_row.dribbles_total += 1
                if outcome == "successful":
                    dribbler_row.dribbles_completed += 1
                result.dribble_events.append(
                    {
                        "timestamp": timestamps[run_start],
                        "track_id": owner_track,
                        "opponent_track_id": nearest_opponent,
                        "outcome": outcome,
                        "description": "Rough dribble estimate: sustained ball carry past a nearby opponent",
                    }
                )
        run_start = run_end + 1

    result.player_rows = [rows[track_id].as_dict() for track_id in sorted(rows)]
    return result


_ADVANCED_STAT_FIELDS = (
    "passes_total",
    "passes_completed",
    "passes_short",
    "passes_long",
    "shots",
    "xg",
    "duels_total",
    "duels_won",
    "dribbles_total",
    "dribbles_completed",
)


def compute_and_store_player_stats(match_id: int, db: Session) -> dict:
    """Recompute duel/dribble events and per-identity advanced stats for a match.

    Post-processing only: reads existing detections + Phase 4/5b/5d events, never
    the video. Idempotent - previous ``duel``/``dribble`` events and
    ``match_player_stats`` rows for the match are replaced.
    """
    match = db.get(Match, match_id)
    if match is None:
        raise ValueError("Match not found")

    video = db.scalar(select(Video).where(Video.match_id == match_id).order_by(Video.id))
    if video is None:
        raise ValueError("No video found")

    detections = db.scalars(
        select(Detection)
        .where(Detection.video_id == video.id)
        .order_by(Detection.frame_timestamp, Detection.id)
    ).all()
    records = [
        {
            "class": item.class_name,
            "track_id": item.track_id,
            "timestamp": item.frame_timestamp,
            "box": item.bounding_box,
            "cluster": item.team_color_cluster,
        }
        for item in detections
    ]
    pass_events = [
        {"event_type": event.event_type, "timestamp": event.timestamp_seconds, "track_id": event.track_id}
        for event in db.scalars(
            select(Event).where(
                Event.match_id == match_id,
                Event.event_type.in_(["pass", "possession_loss"]),
            )
        ).all()
    ]
    shot_events = [
        {"timestamp": event.timestamp_seconds, "track_id": event.track_id, "xg": event.xg}
        for event in db.scalars(
            select(Event).where(Event.match_id == match_id, Event.event_type == "shot")
        ).all()
    ]
    cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}

    computed = compute_advanced_stats(
        records, pass_events, shot_events, video.width or 1, video.height or 1, cluster_roles
    )

    # Replace derived duel/dribble events.
    db.execute(
        delete(Event).where(Event.match_id == match_id, Event.event_type.in_(["duel", "dribble"]))
    )
    for duel in computed.duel_events:
        db.add(
            Event(
                match_id=match_id,
                video_id=video.id,
                event_type="duel",
                timestamp_seconds=duel["timestamp"],
                track_id=duel["winner_track_id"],
                related_track_id=duel["loser_track_id"],
                outcome="won",
                description=duel["description"],
                manually_verified=False,
            )
        )
    for dribble in computed.dribble_events:
        db.add(
            Event(
                match_id=match_id,
                video_id=video.id,
                event_type="dribble",
                timestamp_seconds=dribble["timestamp"],
                track_id=dribble["track_id"],
                related_track_id=dribble["opponent_track_id"],
                outcome=dribble["outcome"],
                description=dribble["description"],
                manually_verified=False,
            )
        )

    # Aggregate per-track rows onto whole-match identities.
    link_by_track = {
        link.track_id: link.match_player_id
        for link in db.scalars(select(TrackPlayerLink).where(TrackPlayerLink.match_id == match_id)).all()
    }
    per_identity: dict[int, dict] = {}
    unlinked_tracks = 0
    for track_row in computed.player_rows:
        match_player_id = link_by_track.get(track_row["track_id"])
        if match_player_id is None:
            unlinked_tracks += 1
            continue
        bucket = per_identity.setdefault(match_player_id, {field: 0 for field in _ADVANCED_STAT_FIELDS})
        for field_name in _ADVANCED_STAT_FIELDS:
            bucket[field_name] += track_row[field_name]

    db.execute(delete(MatchPlayerStats).where(MatchPlayerStats.match_id == match_id))
    db.flush()
    for match_player_id, bucket in per_identity.items():
        db.add(
            MatchPlayerStats(
                match_id=match_id,
                match_player_id=match_player_id,
                passes_total=bucket["passes_total"],
                passes_completed=bucket["passes_completed"],
                passes_short=bucket["passes_short"],
                passes_long=bucket["passes_long"],
                shots=bucket["shots"],
                xg=round(bucket["xg"], 4),
                duels_total=bucket["duels_total"],
                duels_won=bucket["duels_won"],
                dribbles_total=bucket["dribbles_total"],
                dribbles_completed=bucket["dribbles_completed"],
            )
        )
    db.flush()

    return {
        "match_id": match_id,
        "identities_with_stats": len(per_identity),
        "unlinked_tracks": unlinked_tracks,
        "duels_detected": len(computed.duel_events),
        "dribbles_detected": len(computed.dribble_events),
        "dribbles_successful": sum(1 for event in computed.dribble_events if event["outcome"] == "successful"),
        "passes_total": sum(row["passes_total"] for row in computed.player_rows),
        "passes_completed": sum(row["passes_completed"] for row in computed.player_rows),
        "passes_short": sum(row["passes_short"] for row in computed.player_rows),
        "passes_long": sum(row["passes_long"] for row in computed.player_rows),
        "shots_assigned": sum(row["shots"] for row in computed.player_rows),
        "players": read_player_stats(db, match_id)["players"],
        "note": (
            "Passes/shots reuse heuristic events; duels and dribbles are new, "
            "unvalidated heuristics from box positions only - low accuracy, "
            "experimental."
        ),
    }


def read_player_stats(db: Session, match_id: int) -> dict:
    """Serialise stored per-identity stats joined with identity + base metrics."""
    match = db.get(Match, match_id)
    if match is None:
        raise ValueError("Match not found")

    identities = {
        identity.id: identity
        for identity in db.scalars(
            select(MatchPlayer).where(MatchPlayer.match_id == match_id)
        ).all()
    }
    cluster_roles = {item.cluster_id: item.role for item in match.team_cluster_assignments}
    stats_by_identity = {
        stat.match_player_id: stat
        for stat in db.scalars(
            select(MatchPlayerStats).where(MatchPlayerStats.match_id == match_id)
        ).all()
    }

    # Base movement metrics are stored per track_id; fold them onto the identity.
    base_by_identity: dict[int, dict] = defaultdict(
        lambda: {"touches": 0, "distance_meters": 0.0, "avg_speeds": [], "max_speeds": []}
    )
    for metric in db.scalars(
        select(PlayerMetric).where(
            PlayerMetric.match_id == match_id, PlayerMetric.match_player_id.is_not(None)
        )
    ).all():
        bucket = base_by_identity[metric.match_player_id]
        bucket["touches"] += metric.touches
        bucket["distance_meters"] += metric.distance_meters
        bucket["avg_speeds"].append(metric.average_speed_mps)
        bucket["max_speeds"].append(metric.max_speed_mps)

    players = []
    for identity_id, identity in sorted(identities.items()):
        stat = stats_by_identity.get(identity_id)
        base = base_by_identity.get(identity_id)
        role = cluster_roles.get(identity.team_color_cluster, "unknown")
        players.append(
            {
                "match_player_id": identity_id,
                "team_role": role,
                "jersey_number": identity.jersey_number,
                "is_unknown": identity.is_unknown,
                "label": _identity_label(role, identity),
                "touches": base["touches"] if base else 0,
                "distance_meters": round(base["distance_meters"], 2) if base else 0.0,
                # Averaged over the identity's tracks (not detection-weighted) -
                # a rough figure once tracks are stitched.
                "average_speed_mps": round(sum(base["avg_speeds"]) / len(base["avg_speeds"]), 2)
                if base and base["avg_speeds"]
                else 0.0,
                "max_speed_mps": round(max(base["max_speeds"]), 2) if base and base["max_speeds"] else 0.0,
                "passes_total": stat.passes_total if stat else 0,
                "passes_completed": stat.passes_completed if stat else 0,
                "passes_short": stat.passes_short if stat else 0,
                "passes_long": stat.passes_long if stat else 0,
                "shots": stat.shots if stat else 0,
                "xg": round(stat.xg, 4) if stat else 0.0,
                "duels_total": stat.duels_total if stat else 0,
                "duels_won": stat.duels_won if stat else 0,
                "dribbles_total": stat.dribbles_total if stat else 0,
                "dribbles_completed": stat.dribbles_completed if stat else 0,
            }
        )
    return {"match_id": match_id, "players": players}


def _identity_label(role: str, identity: MatchPlayer) -> str:
    if identity.jersey_number is not None:
        return f"{role.title()} · #{identity.jersey_number}"
    return f"{role.title()} · Unknown ({identity.id})"
