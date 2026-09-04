from collections import defaultdict
from dataclasses import dataclass
from math import atan2, exp, hypot, pi


PITCH_LENGTH_METERS = 105.0
PITCH_WIDTH_METERS = 68.0
SHOT_SPEED_THRESHOLD_MPS = 8.0
GOAL_APPROACH_DISTANCE_METERS = 30.0
GOAL_CENTER_Y = 34.0
PLAYER_BALL_DISTANCE_METERS = 12.0


@dataclass
class ShotResult:
    timestamp: float
    track_id: int | None
    team_role: str
    xg: float
    position_x: float
    position_y: float
    description: str


def to_pitch_position(box: dict[str, float], image_width: int, image_height: int) -> tuple[float, float]:
    center_x = (box["x"] + box["width"] / 2) / max(image_width, 1)
    center_y = (box["y"] + box["height"] / 2) / max(image_height, 1)
    return center_x * PITCH_LENGTH_METERS, center_y * PITCH_WIDTH_METERS


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
    """Heuristically detect fast goal-directed ball segments in sparse stored samples."""
    cluster_roles = cluster_roles or {}
    balls = sorted(ball_records, key=lambda record: record["timestamp"])
    players_by_time: dict[float, list[dict]] = defaultdict(list)
    for player in player_records:
        players_by_time[player["timestamp"]].append(player)

    shots: list[ShotResult] = []
    for current, following in zip(balls, balls[1:]):
        elapsed = following["timestamp"] - current["timestamp"]
        if elapsed <= 0:
            continue
        current_x, current_y = to_pitch_position(current["box"], image_width, image_height)
        following_x, following_y = to_pitch_position(following["box"], image_width, image_height)
        delta_x = following_x - current_x
        delta_y = following_y - current_y
        speed = hypot(delta_x, delta_y) / elapsed
        toward_right = delta_x > 0 and delta_x >= abs(delta_y) * 0.75
        toward_left = delta_x < 0 and abs(delta_x) >= abs(delta_y) * 0.75
        goal_x = PITCH_LENGTH_METERS if toward_right else 0.0 if toward_left else None
        if goal_x is None or speed < SHOT_SPEED_THRESHOLD_MPS:
            continue
        distance_to_goal = abs(goal_x - current_x)
        if distance_to_goal > GOAL_APPROACH_DISTANCE_METERS:
            continue
        angle = atan2(current_y - GOAL_CENTER_Y, distance_to_goal)
        xg = expected_goals(distance_to_goal, angle)
        shooter = min(
            players_by_time.get(current["timestamp"], []),
            key=lambda player: hypot(
                to_pitch_position(player["box"], image_width, image_height)[0] - current_x,
                to_pitch_position(player["box"], image_width, image_height)[1] - current_y,
            ),
            default=None,
        )
        shooter_distance = None
        if shooter is not None:
            shooter_x, shooter_y = to_pitch_position(shooter["box"], image_width, image_height)
            shooter_distance = hypot(shooter_x - current_x, shooter_y - current_y)
        if shooter is None or shooter_distance is None or shooter_distance > PLAYER_BALL_DISTANCE_METERS:
            continue
        team_role = cluster_roles.get(shooter.get("cluster"), "unknown")
        if team_role not in ("home", "away"):
            continue
        shots.append(
            ShotResult(
                timestamp=current["timestamp"],
                track_id=shooter.get("track_id"),
                team_role=team_role,
                xg=xg,
                position_x=round(current_x, 3),
                position_y=round(current_y, 3),
                description="Heuristic shot estimate from sparse ball direction and speed",
            )
        )
    return shots