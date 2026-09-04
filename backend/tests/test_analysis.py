from app.services.analysis import analyze_records


def test_analysis_calculates_tracking_possession_touches_and_events():
    records = [
        {"id": 1, "timestamp": 0, "class": "player", "cluster": 0, "box": {"x": 10, "y": 10, "width": 20, "height": 40}, "jersey_number": 7},
        {"id": 2, "timestamp": 0, "class": "player", "cluster": 1, "box": {"x": 80, "y": 10, "width": 20, "height": 40}, "jersey_number": 9},
        {"id": 3, "timestamp": 0, "class": "ball", "cluster": None, "box": {"x": 18, "y": 25, "width": 4, "height": 4}},
        {"id": 4, "timestamp": 1, "class": "player", "cluster": 0, "box": {"x": 11, "y": 10, "width": 20, "height": 40}, "jersey_number": 7},
        {"id": 5, "timestamp": 1, "class": "player", "cluster": 1, "box": {"x": 80, "y": 10, "width": 20, "height": 40}, "jersey_number": 9},
        {"id": 6, "timestamp": 1, "class": "ball", "cluster": None, "box": {"x": 82, "y": 25, "width": 4, "height": 4}},
    ]

    result = analyze_records(records, image_width=100, image_height=100, cluster_roles={0: "home", 1: "away"})

    assert result.track_ids[1] == result.track_ids[4]
    assert result.track_ids[2] == result.track_ids[5]
    assert result.home_possession_pct == 50
    assert result.away_possession_pct == 50
    assert sum(player["touches"] for player in result.players) == 2
    assert {event["event_type"] for event in result.events} == {"possession_loss"}
    assert all(player["distance_meters"] >= 0 for player in result.players)