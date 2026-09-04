from app.services.player_options import build_player_options


def test_player_options_use_most_common_jersey_number_and_cluster():
    options = build_player_options(
        [
            {"class": "player", "track_id": 7, "team_color_cluster": 1, "jersey_number": 10},
            {"class": "player", "track_id": 7, "team_color_cluster": 1, "jersey_number": 11},
            {"class": "player", "track_id": 7, "team_color_cluster": 1, "jersey_number": 10},
            {"class": "player", "track_id": 8, "team_color_cluster": 0, "jersey_number": None},
        ],
        cluster_roles={0: "home", 1: "away"},
    )

    assert options[0]["jersey_number"] == 10
    assert options[0]["team_role"] == "away"
    assert options[0]["label"] == "Away · #10"
    assert options[1]["label"] == "Home · Unknown #8"