from app.services.passing_network import build_passing_network


def test_passing_network_pairs_previous_owner_with_pass_recipient():
    records = [
        {"class": "player", "track_id": 1, "timestamp": 0, "cluster": 0, "jersey_number": 8, "box": {"x": 10, "y": 10, "width": 10, "height": 20}},
        {"class": "player", "track_id": 2, "timestamp": 0, "cluster": 0, "jersey_number": 9, "box": {"x": 70, "y": 10, "width": 10, "height": 20}},
        {"class": "ball", "track_id": None, "timestamp": 0, "cluster": None, "jersey_number": None, "box": {"x": 12, "y": 16, "width": 4, "height": 4}},
        {"class": "player", "track_id": 1, "timestamp": 1, "cluster": 0, "jersey_number": 8, "box": {"x": 10, "y": 10, "width": 10, "height": 20}},
        {"class": "player", "track_id": 2, "timestamp": 1, "cluster": 0, "jersey_number": 9, "box": {"x": 70, "y": 10, "width": 10, "height": 20}},
        {"class": "ball", "track_id": None, "timestamp": 1, "cluster": None, "jersey_number": None, "box": {"x": 72, "y": 16, "width": 4, "height": 4}},
    ]

    network = build_passing_network(records, [{"track_id": 2, "timestamp": 1}], 100, 100, {0: "home"})

    assert network["home"]["edges"] == [{
        "source_track_id": 1,
        "target_track_id": 2,
        "pass_count": 1,
        "source_x": 0.15,
        "source_y": 0.2,
        "target_x": 0.75,
        "target_y": 0.2,
    }]