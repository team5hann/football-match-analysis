from app.services.shots import detect_shots


def test_shot_detection_returns_bounded_xg_for_goalward_ball_motion():
    ball_records = [
        {"timestamp": 0, "box": {"x": 76, "y": 48, "width": 2, "height": 2}},
        {"timestamp": 1, "box": {"x": 96, "y": 48, "width": 2, "height": 2}},
    ]
    player_records = [
        {"timestamp": 0, "track_id": 4, "cluster": 0, "box": {"x": 74, "y": 46, "width": 8, "height": 12}},
    ]

    shots = detect_shots(ball_records, player_records, 100, 100, {0: "home"})

    assert len(shots) == 1
    assert shots[0].team_role == "home"
    assert shots[0].track_id == 4
    assert 0 <= shots[0].xg <= 1