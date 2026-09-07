from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.analysis import PlayerMetric
from app.models.detection import Detection
from app.models.event import Event
from app.models.match import Match
from app.models.player_identity import MatchPlayer, TrackPlayerLink
from app.models.player_stats import MatchPlayerStats
from app.models.team_cluster import TeamClusterAssignment
from app.models.video import Video

_SessionLocal = sessionmaker(bind=create_engine(get_settings().database_url), autoflush=False)

CATEGORIES = ("attack", "passing", "defence", "pressing", "transitions")


def _seed_match(*, with_stats: bool) -> int:
    s = _SessionLocal()
    try:
        match = Match()
        s.add(match)
        s.flush()
        video = Video(match_id=match.id, original_filename="c.mp4", stored_filename="c.mp4",
                      file_path="/tmp/c.mp4", width=1920, height=1080, duration_seconds=90.0)
        s.add(video)
        s.flush()
        s.add(TeamClusterAssignment(match_id=match.id, cluster_id=0, role="home"))
        s.add(TeamClusterAssignment(match_id=match.id, cluster_id=1, role="away"))
        # a couple of detections so _load_match_inputs has something to chew on
        for t in (0.0, 0.1, 0.2):
            s.add(Detection(video_id=video.id, frame_timestamp=t, bounding_box={"x": 1, "y": 1, "width": 5, "height": 10},
                            class_name="player", confidence=0.9, track_id=1, team_color_cluster=0))
            s.add(Detection(video_id=video.id, frame_timestamp=t, bounding_box={"x": 50, "y": 1, "width": 5, "height": 10},
                            class_name="player", confidence=0.9, track_id=2, team_color_cluster=1))
            s.add(Detection(video_id=video.id, frame_timestamp=t, bounding_box={"x": 10, "y": 5, "width": 2, "height": 2},
                            class_name="ball", confidence=0.9))
        if with_stats:
            for cluster, jersey, (pt, pc, dt, dw, sh, xg) in [
                (0, 9, (40, 32, 10, 7, 3, 0.4)),
                (1, 9, (20, 9, 10, 2, 5, 0.9)),
            ]:
                identity = MatchPlayer(match_id=match.id, team_color_cluster=cluster, jersey_number=jersey,
                                       confidence=0.9, is_unknown=False)
                s.add(identity)
                s.flush()
                s.add(MatchPlayerStats(match_id=match.id, match_player_id=identity.id, passes_total=pt,
                                       passes_completed=pc, passes_short=pt, duels_total=dt, duels_won=dw,
                                       shots=sh, xg=xg, dribbles_total=2, dribbles_completed=1))
                s.add(PlayerMetric(match_id=match.id, track_id=100 + cluster, match_player_id=identity.id,
                                   team_color_cluster=cluster, jersey_number=jersey, touches=20,
                                   distance_meters=800.0, average_speed_mps=3.5, max_speed_mps=8.0))
                s.add(TrackPlayerLink(match_id=match.id, track_id=100 + cluster, match_player_id=identity.id))
            s.add(Event(match_id=match.id, event_type="possession_loss", timestamp_seconds=5.0, track_id=100))
            s.add(Event(match_id=match.id, event_type="possession_loss", timestamp_seconds=6.0, track_id=100))
        s.commit()
        return match.id
    finally:
        s.close()


def test_radar_five_scores_in_range_for_both_teams(client):
    match_id = _seed_match(with_stats=True)
    body = client.get(f"/api/matches/{match_id}/radar").json()

    assert set(body["home"]["scores"]) == set(CATEGORIES)
    for role in ("home", "away"):
        for category in CATEGORIES:
            value = body["home" if role == "home" else "away"]["scores"][category]
            assert 0.0 <= value <= 10.0, (role, category, value)

    # home passed 32/40 -> passing 8.0; away 9/20 -> 4.5
    assert body["home"]["scores"]["passing"] == 8.0
    assert body["away"]["scores"]["passing"] == 4.5
    # home won 7/10 duels -> defence 7.0; away 2/10 -> 2.0
    assert body["home"]["scores"]["defence"] == 7.0
    assert body["away"]["scores"]["defence"] == 2.0
    assert isinstance(body["notes"]["pressing"], str) and "APPROXIMATION" in body["notes"]["pressing"]


def test_radar_does_not_crash_without_any_stats(client):
    match_id = _seed_match(with_stats=False)
    body = client.get(f"/api/matches/{match_id}/radar").json()
    for role in ("home", "away"):
        assert all(body[role]["scores"][c] == 0.0 for c in ("attack", "passing", "defence", "pressing"))
        assert 0.0 <= body[role]["scores"]["transitions"] <= 10.0


def test_radar_missing_match_is_404(client):
    assert client.get("/api/matches/999999/radar").status_code == 404
