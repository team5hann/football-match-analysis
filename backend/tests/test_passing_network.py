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


def _p(track_id, jersey, t, x):
    return {"class": "player", "track_id": track_id, "timestamp": t, "cluster": 0,
            "jersey_number": jersey, "box": {"x": x, "y": 10, "width": 10, "height": 20}}


def test_passing_network_uses_identities_for_nodes_and_recorded_sender():
    # tracks 1 & 3 are the SAME player (identity 500); track 2 is identity 600.
    records = [
        _p(1, 8, 0, 10), _p(2, 9, 0, 70),
        _p(3, 8, 1, 12), _p(2, 9, 1, 70),
        _p(3, 8, 2, 14), _p(2, 9, 2, 70),
    ]
    match_player_by_track = {1: 500, 3: 500, 2: 600}
    # pass event carries its sender explicitly (track 3 -> track 2)
    events = [{"track_id": 2, "sender_track_id": 3, "timestamp": 1}]

    network = build_passing_network(records, events, 100, 100, {0: "home"}, match_player_by_track)

    home = network["home"]
    # one node per real player, not per track fragment
    assert {node["track_id"] for node in home["nodes"]} == {500, 600}
    id_500 = next(node for node in home["nodes"] if node["track_id"] == 500)
    # tracks 1 (t0) + 3 (t1, t2) folded into one node
    assert id_500["match_player_id"] == 500 and id_500["detections_count"] == 3

    assert len(home["edges"]) == 1
    edge = home["edges"][0]
    assert edge["source_track_id"] == 500 and edge["target_track_id"] == 600
    assert edge["pass_count"] == 1