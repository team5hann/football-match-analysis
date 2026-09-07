from app.services.analysis import analyze_records


# track_id now arrives from ByteTrack on each detection; analysis just reads it.
HOME_TRACK = 100
AWAY_TRACK = 200


def _home(t, ident):
    return {"id": ident, "timestamp": t, "class": "player", "cluster": 0, "track_id": HOME_TRACK,
            "box": {"x": 10, "y": 10, "width": 20, "height": 40}, "jersey_number": 7}


def _away(t, ident):
    return {"id": ident, "timestamp": t, "class": "player", "cluster": 1, "track_id": AWAY_TRACK,
            "box": {"x": 80, "y": 10, "width": 20, "height": 40}, "jersey_number": 9}


def _ball(t, ident, cx):
    return {"id": ident, "timestamp": t, "class": "ball", "cluster": None, "track_id": None,
            "box": {"x": cx, "y": 25, "width": 4, "height": 4}}


def test_analysis_debounced_possession_change_emits_one_turnover_with_sender():
    records = []
    ident = 1
    # 3 frames: ball with the home player (a confirmed possession spell)
    for step, t in enumerate((0.0, 0.1, 0.2)):
        records += [_home(t, ident), _away(t, ident + 1), _ball(t, ident + 2, 18)]
        ident += 3
    # 3 frames: ball with the away player (a second confirmed spell, ~70 m away)
    for step, t in enumerate((0.3, 0.4, 0.5)):
        records += [_home(t, ident), _away(t, ident + 1), _ball(t, ident + 2, 82)]
        ident += 3

    result = analyze_records(records, image_width=100, image_height=100, cluster_roles={0: "home", 1: "away"}, possession_confirm_frames=3, min_change_meters=4.0)

    # analysis passes the ByteTrack ids straight through
    assert result.track_ids[1] == HOME_TRACK and result.track_ids[10] == HOME_TRACK
    assert result.track_ids[2] == AWAY_TRACK and result.track_ids[11] == AWAY_TRACK

    assert result.home_possession_pct == 50 and result.away_possession_pct == 50

    # exactly one debounced event: home lost the ball to away
    assert len(result.events) == 1
    event = result.events[0]
    assert event["event_type"] == "possession_loss"
    assert event["track_id"] == AWAY_TRACK          # new owner
    assert event["sender_track_id"] == HOME_TRACK   # who lost it


def test_analysis_ignores_single_frame_possession_flicker():
    # home owns for 4 frames, one flicker frame where the ball snaps to the away
    # player, then home again for 4 frames -> the flicker must not be a turnover.
    records = []
    ident = 1
    plan = [(0.0, 18), (0.1, 18), (0.2, 18), (0.3, 18), (0.4, 82), (0.5, 18), (0.6, 18), (0.7, 18), (0.8, 18)]
    for t, cx in plan:
        records += [_home(t, ident), _away(t, ident + 1), _ball(t, ident + 2, cx)]
        ident += 3

    result = analyze_records(records, image_width=100, image_height=100, cluster_roles={0: "home", 1: "away"}, possession_confirm_frames=3, min_change_meters=4.0)
    assert result.events == []
