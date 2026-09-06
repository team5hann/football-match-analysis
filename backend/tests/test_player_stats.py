"""Sanity tests for the advanced player-stats heuristics.

These check that each category computes without crashing and returns a
sensible SHAPE of data - not that the numbers are accurate. Duels and
dribbles in particular are rough, unvalidated heuristics.
"""
from app.services.player_stats import compute_advanced_stats


def _player(track_id, cluster, timestamp, cx, cy):
    return {
        "class": "player",
        "track_id": track_id,
        "timestamp": timestamp,
        "cluster": cluster,
        "box": {"x": cx - 1, "y": cy - 2, "width": 2, "height": 4},
    }


def _ball(timestamp, cx, cy):
    return {"class": "ball", "track_id": None, "timestamp": timestamp, "cluster": None,
            "box": {"x": cx - 0.5, "y": cy - 0.5, "width": 1, "height": 1}}


ROLES = {0: "home", 1: "away"}


def test_passes_split_into_short_long_and_possession_loss_is_a_failed_pass():
    # home #1 (track 1) has the ball at t0, home #2 (track 2) at t1 (a completed
    # pass), then away #3 (track 3) has it at t2 (a possession loss for track 2).
    records = [
        _player(1, 0, 0.0, 20, 34), _ball(0.0, 20, 34), _player(2, 0, 0.0, 60, 34), _player(3, 1, 0.0, 90, 10),
        _player(1, 0, 1.0, 20, 34), _ball(1.0, 60, 34), _player(2, 0, 1.0, 60, 34), _player(3, 1, 1.0, 90, 10),
        _player(1, 0, 2.0, 20, 34), _ball(2.0, 90, 12), _player(2, 0, 2.0, 60, 34), _player(3, 1, 2.0, 90, 12),
    ]
    pass_events = [
        {"event_type": "pass", "timestamp": 1.0, "track_id": 2},
        {"event_type": "possession_loss", "timestamp": 2.0, "track_id": 3},
    ]

    result = compute_advanced_stats(records, pass_events, [], 100, 100, ROLES)
    by_track = {row["track_id"]: row for row in result.player_rows}

    # track 1 made one completed pass (to track 2 ~42 m away -> long)
    assert by_track[1]["passes_total"] == 1
    assert by_track[1]["passes_completed"] == 1
    assert by_track[1]["passes_short"] + by_track[1]["passes_long"] == 1
    assert by_track[1]["passes_long"] == 1

    # track 2 lost possession -> counted as an attempted but NOT completed pass
    assert by_track[2]["passes_total"] == 1
    assert by_track[2]["passes_completed"] == 0

    for row in result.player_rows:
        assert row["passes_completed"] <= row["passes_total"]
        assert row["passes_short"] + row["passes_long"] == row["passes_total"]


def test_shots_aggregate_count_and_xg_per_player():
    shot_events = [
        {"timestamp": 5.0, "track_id": 7, "xg": 0.12},
        {"timestamp": 9.0, "track_id": 7, "xg": 0.30},
        {"timestamp": 11.0, "track_id": 9, "xg": 0.05},
    ]
    result = compute_advanced_stats([], [], shot_events, 100, 100, ROLES)
    by_track = {row["track_id"]: row for row in result.player_rows}

    assert by_track[7]["shots"] == 2
    assert abs(by_track[7]["xg"] - 0.42) < 1e-6
    assert by_track[9]["shots"] == 1


def test_duel_produces_winner_and_loser_and_aggregates():
    # t0: home track 1 and away track 2 both hug the ball and each other.
    # t1: they separate. t2: home clearly owns -> home wins the duel.
    records = [
        _ball(0.0, 50, 34), _player(1, 0, 0.0, 49.6, 34), _player(2, 1, 0.0, 50.4, 34),
        _ball(1.0, 50, 34), _player(1, 0, 1.0, 45, 34), _player(2, 1, 1.0, 62, 34),
        _ball(2.0, 50, 34), _player(1, 0, 2.0, 50, 34), _player(2, 1, 2.0, 88, 34),
    ]
    result = compute_advanced_stats(records, [], [], 100, 100, ROLES)

    assert len(result.duel_events) == 1
    duel = result.duel_events[0]
    assert duel["winner_track_id"] == 1 and duel["loser_track_id"] == 2

    by_track = {row["track_id"]: row for row in result.player_rows}
    assert by_track[1]["duels_total"] == 1 and by_track[1]["duels_won"] == 1
    assert by_track[2]["duels_total"] == 1 and by_track[2]["duels_won"] == 0


def test_dribble_detected_with_outcome_and_aggregates():
    # home track 1 carries the ball across 4 samples (~40 m) with an away
    # defender ~3 m away the whole time, and keeps it -> successful dribble.
    records = []
    for index, x in enumerate((10, 22, 36, 50)):
        t = float(index)
        records += [_ball(t, x, 34), _player(1, 0, t, x, 34), _player(2, 1, t, x + 3, 34)]
    result = compute_advanced_stats(records, [], [], 100, 100, ROLES)

    assert len(result.dribble_events) == 1
    dribble = result.dribble_events[0]
    assert dribble["track_id"] == 1
    assert dribble["outcome"] in ("successful", "unsuccessful")

    by_track = {row["track_id"]: row for row in result.player_rows}
    assert by_track[1]["dribbles_total"] == 1
    assert by_track[1]["dribbles_completed"] in (0, 1)


def test_empty_inputs_do_not_crash():
    result = compute_advanced_stats([], [], [], 100, 100, ROLES)
    assert result.player_rows == []
    assert result.duel_events == [] and result.dribble_events == []
