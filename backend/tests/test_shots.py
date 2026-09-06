from app.services.shots import detect_shots

# image size == pitch size so box-centre pixels are already metres.
IMG_W, IMG_H = 105, 68


def _ball(t, cx, cy=34):
    return {"timestamp": t, "box": {"x": cx - 1, "y": cy - 1, "width": 2, "height": 2}}


def _player(t, track_id, cx, cy=34, cluster=0):
    return {"timestamp": t, "track_id": track_id, "cluster": cluster,
            "box": {"x": cx - 4, "y": cy - 6, "width": 8, "height": 12}}


def test_shot_detection_flags_fast_goalward_ball_near_goal():
    # ball driven from 25 m out straight into the right goal over 0.6 s (~33 m/s)
    ball_records = [_ball(0.0, 80), _ball(0.2, 87), _ball(0.4, 94), _ball(0.6, 100)]
    player_records = [_player(0.0, 4, 78)]

    shots = detect_shots(ball_records, player_records, IMG_W, IMG_H, {0: "home"})

    assert len(shots) == 1
    assert shots[0].team_role == "home"
    assert shots[0].track_id == 4
    assert 0 <= shots[0].xg <= 1
    assert abs(shots[0].position_x - 80) < 2


def test_ordinary_pace_pass_is_not_a_shot():
    # same geometry, but ~10 m/s (a pass): 80 -> 86 over 0.6 s
    ball_records = [_ball(0.0, 80), _ball(0.2, 82), _ball(0.4, 84), _ball(0.6, 86)]
    player_records = [_player(0.0, 4, 78)]

    assert detect_shots(ball_records, player_records, IMG_W, IMG_H, {0: "home"}) == []


def test_single_teleporting_ball_detection_is_not_a_shot():
    # one frame where the ball detector jumps across the pitch, then settles -
    # the windowed measurement must not read this as a 100+ m/s "shot".
    ball_records = [_ball(0.0, 80), _ball(0.05, 5), _ball(0.10, 80), _ball(0.6, 81)]
    player_records = [_player(0.0, 4, 78)]

    assert detect_shots(ball_records, player_records, IMG_W, IMG_H, {0: "home"}) == []


def test_midfield_fast_ball_is_not_a_shot():
    # fast + straight, but starts 50 m from either goal -> not a shot origin
    ball_records = [_ball(0.0, 50), _ball(0.2, 58), _ball(0.4, 66), _ball(0.6, 72)]
    player_records = [_player(0.0, 4, 49)]

    assert detect_shots(ball_records, player_records, IMG_W, IMG_H, {0: "home"}) == []
