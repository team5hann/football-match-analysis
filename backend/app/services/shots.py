from collections import defaultdict
from dataclasses import dataclass
from math import atan2, exp, hypot, pi

from app.services.pitch import pitch_xy


PITCH_LENGTH_METERS = 105.0
PITCH_WIDTH_METERS = 68.0
GOAL_CENTER_Y = 34.0

# --- shot-detection thresholds (all heuristic, all in real metres) ----------
# This is still just "fast, straight, goalward ball movement near a goal" - it
# does NOT classify the action, so a hard driven pass toward goal can still slip
# through and a deflected shot can be missed. The thresholds below are set from
# real football references: ordinary passes travel well under ~15 m/s, medium
# shots ~15-25 m/s, hard shots ~25-35 m/s, with the very hardest ever recorded
# around ~38 m/s. Anything faster than that in this data is a ball-tracking
# artefact (the sparse ball detector teleports), so speed has an UPPER bound too.
SHOT_WINDOW_SECONDS = 0.7          # motion is measured over a window, not one frame pair
SHOT_MIN_SPEED_MPS = 19.0         # comfortably above the driven-pass range (~<15 m/s)
SHOT_MAX_SPEED_MPS = 38.0         # above this it is tracking noise, not a ball
SHOT_MIN_TRAVEL_METERS = 9.0      # the ball has to actually cover ground...
SHOT_MAX_TRAVEL_METERS = 24.0    # ...but not implausibly much over ~0.7 s
SHOT_MAX_ORIGIN_DISTANCE_METERS = 30.0   # must START within realistic shooting range of the goal
SHOT_MAX_END_DISTANCE_METERS = 20.0      # ...and END up near that goal
SHOT_GOALWARD_PROGRESS_RATIO = 0.7       # >=70% of the travel must be net progress toward the goal line
SHOT_DIRECTION_RATIO = 1.5               # x-motion must dominate y-motion (goalward, not a cross)
PLAYER_BALL_DISTANCE_METERS = 9.0        # the "shooter" must be close to the ball at the strike
# Overlapping windows over one strike collapse into a single shot per shooter,
# and any two shots closer than the global gap are treated as one event (the
# ball is often near two players during a strike, so per-shooter dedup alone
# still double-counts).
SHOT_DEDUP_SECONDS = 3.0
SHOT_GLOBAL_DEDUP_SECONDS = 2.5


@dataclass
class ShotResult:
    timestamp: float
    track_id: int | None
    team_role: str
    xg: float
    position_x: float
    position_y: float
    description: str


def to_pitch_position(record: dict, image_width: int, image_height: int) -> tuple[float, float]:
    """Pitch metres for a detection record: real homography position when the
    pitch-homography pass has run, otherwise the old image-linear estimate."""
    return pitch_xy(record, image_width, image_height)


def expected_goals(distance_meters: float, angle_radians: float) -> float:
    """Return a deliberately simple distance/angle xG approximation, not a trained model."""
    distance_factor = exp(-distance_meters / 28.0)
    angle_factor = max(0.0, 1.0 - abs(angle_radians) / (pi / 2))
    return round(max(0.0, min(1.0, 0.04 + 0.42 * distance_factor * angle_factor)), 4)


def detect_shots(
    ball_records: list[dict],
    player_records: list[dict],
    image_width: int,
    image_height: int,
    cluster_roles: dict[int, str] | None = None,
) -> list[ShotResult]:
    """Heuristically flag fast, straight, goalward ball movement near a goal.

    Not an action classifier - see the threshold comments above. Ball motion is
    measured over a short window (``SHOT_WINDOW_SECONDS``) rather than a single
    detection pair, so a lone teleporting ball detection no longer reads as a
    100+ m/s "shot".
    """
    cluster_roles = cluster_roles or {}
    balls = sorted(ball_records, key=lambda record: record["timestamp"])
    players_by_time: dict[float, list[dict]] = defaultdict(list)
    for player in player_records:
        players_by_time[player["timestamp"]].append(player)

    positions = [to_pitch_position(ball, image_width, image_height) for ball in balls]

    shots: list[ShotResult] = []
    last_shot_time_by_track: dict[int | None, float] = {}
    for start_index, start_ball in enumerate(balls):
        window_end = start_index
        while (
            window_end + 1 < len(balls)
            and balls[window_end + 1]["timestamp"] - start_ball["timestamp"] <= SHOT_WINDOW_SECONDS
        ):
            window_end += 1
        if window_end - start_index < 2:  # need at least 3 samples in the window
            continue
        elapsed = balls[window_end]["timestamp"] - start_ball["timestamp"]
        if elapsed <= 0:
            continue

        start_x, start_y = positions[start_index]
        end_x, end_y = positions[window_end]
        delta_x = end_x - start_x
        delta_y = end_y - start_y
        travel = hypot(delta_x, delta_y)
        speed = travel / elapsed

        if not (SHOT_MIN_SPEED_MPS <= speed <= SHOT_MAX_SPEED_MPS):
            continue
        if not (SHOT_MIN_TRAVEL_METERS <= travel <= SHOT_MAX_TRAVEL_METERS):
            continue
        if abs(delta_x) < abs(delta_y) * SHOT_DIRECTION_RATIO:
            continue

        goal_x = PITCH_LENGTH_METERS if delta_x > 0 else 0.0
        origin_distance = abs(goal_x - start_x)
        end_distance = abs(goal_x - end_x)
        if origin_distance > SHOT_MAX_ORIGIN_DISTANCE_METERS:
            continue
        if end_distance > SHOT_MAX_END_DISTANCE_METERS:
            continue
        if (origin_distance - end_distance) < SHOT_GOALWARD_PROGRESS_RATIO * travel:
            continue

        angle = atan2(start_y - GOAL_CENTER_Y, max(origin_distance, 1e-6))
        xg = expected_goals(origin_distance, angle)

        shooter = min(
            players_by_time.get(start_ball["timestamp"], []),
            key=lambda player: hypot(
                *(a - b for a, b in zip(to_pitch_position(player, image_width, image_height), (start_x, start_y)))
            ),
            default=None,
        )
        if shooter is None:
            continue
        shooter_x, shooter_y = to_pitch_position(shooter, image_width, image_height)
        if hypot(shooter_x - start_x, shooter_y - start_y) > PLAYER_BALL_DISTANCE_METERS:
            continue
        team_role = cluster_roles.get(shooter.get("cluster"), "unknown")
        if team_role not in ("home", "away"):
            continue

        shooter_track = shooter.get("track_id")
        previous = last_shot_time_by_track.get(shooter_track)
        if previous is not None and start_ball["timestamp"] - previous <= SHOT_DEDUP_SECONDS:
            continue
        if shots and start_ball["timestamp"] - shots[-1].timestamp <= SHOT_GLOBAL_DEDUP_SECONDS:
            continue
        last_shot_time_by_track[shooter_track] = start_ball["timestamp"]
        shots.append(
            ShotResult(
                timestamp=start_ball["timestamp"],
                track_id=shooter_track,
                team_role=team_role,
                xg=xg,
                position_x=round(start_x, 3),
                position_y=round(start_y, 3),
                description="Heuristic shot estimate: fast straight goalward ball movement near a goal",
            )
        )
    return shots