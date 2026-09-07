from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.models.analysis import MatchAnalysisSummary, PlayerMetric
from app.models.match import Match
from app.models.player_identity import MatchPlayer
from app.models.player_stats import MatchPlayerStats
from app.models.team import Team
from app.models.team_cluster import TeamClusterAssignment

_SessionLocal = sessionmaker(bind=create_engine(get_settings().database_url), autoflush=False)


def _seed_two_teams_three_matches() -> int:
    """Team A (home in all 3) vs Team B. Returns team A's id."""
    s = _SessionLocal()
    try:
        team_a = Team(name="Alpha")
        team_b = Team(name="Beta")
        s.add_all([team_a, team_b])
        s.flush()

        specs = [
            # (home_score, away_score, home_possession, jersey -> (passes_total, passes_completed, shots, xg, duels_total, duels_won, touches))
            (2, 0, 60.0, {9: (10, 8, 2, 0.30, 4, 3, 20), 7: (12, 6, 0, 0.0, 2, 1, 15)}),
            (1, 1, 55.0, {9: (8, 7, 1, 0.10, 3, 2, 18)}),
            (0, 3, 40.0, {9: (6, 3, 0, 0.0, 5, 1, 9), 11: (4, 4, 0, 0.0, 0, 0, 7)}),
        ]
        for index, (hs, aWs, poss, jerseys) in enumerate(specs):
            match = Match(home_team_id=team_a.id, away_team_id=team_b.id, home_score=hs, away_score=aWs)
            s.add(match)
            s.flush()
            s.add(TeamClusterAssignment(match_id=match.id, cluster_id=0, role="home", team_id=team_a.id))
            s.add(TeamClusterAssignment(match_id=match.id, cluster_id=1, role="away", team_id=team_b.id))
            s.add(MatchAnalysisSummary(match_id=match.id, home_possession_pct=poss, away_possession_pct=100 - poss))
            for number, (pt, pc, sh, xg, dt, dw, tou) in jerseys.items():
                identity = MatchPlayer(match_id=match.id, team_color_cluster=0, jersey_number=number,
                                       confidence=0.9, is_unknown=False)
                s.add(identity)
                s.flush()
                s.add(MatchPlayerStats(match_id=match.id, match_player_id=identity.id,
                                       passes_total=pt, passes_completed=pc, passes_short=pt, passes_long=0,
                                       shots=sh, xg=xg, duels_total=dt, duels_won=dw))
                s.add(PlayerMetric(match_id=match.id, track_id=1000 + number, match_player_id=identity.id,
                                   team_color_cluster=0, jersey_number=number, touches=tou,
                                   distance_meters=100.0 + tou, average_speed_mps=3.0, max_speed_mps=7.0 + index))
            # an "Unknown" identity with no jersey number - must be counted separately
            s.add(MatchPlayer(match_id=match.id, team_color_cluster=0, jersey_number=None,
                              confidence=0.0, is_unknown=True))
        s.commit()
        return team_a.id
    finally:
        s.close()


def test_team_stats_aggregate_form_and_players(client):
    team_id = _seed_two_teams_three_matches()

    body = client.get(f"/api/teams/{team_id}/stats").json()

    assert body["team_name"] == "Alpha"
    assert body["matches_played"] == 3
    assert body["matches_with_analysis"] == 3
    assert body["wins"] == 1 and body["draws"] == 1 and body["losses"] == 1
    assert body["goals_for"] == 3 and body["goals_against"] == 4
    assert body["avg_possession_pct"] == round((60 + 55 + 40) / 3, 2)
    # total xG = 0.30 + 0.10 = 0.40 (match 3 shots have xg 0)
    assert abs(body["total_xg"] - 0.40) < 1e-6
    assert body["total_shots"] == 3
    assert body["players_without_jersey_number"] == 3

    assert [row["match_id"] for row in body["trend"]] == sorted(row["match_id"] for row in body["trend"])
    assert body["trend"][0]["result"] == "win" and body["trend"][0]["side"] == "home"

    by_number = {p["jersey_number"]: p for p in body["players"]}
    assert set(by_number) == {7, 9, 11}
    # #9 played all three matches
    nine = by_number[9]
    assert nine["matches_played"] == 3
    assert nine["passes_total"] == 10 + 8 + 6
    assert nine["passes_completed"] == 8 + 7 + 3
    assert nine["shots"] == 3
    assert abs(nine["xg"] - 0.40) < 1e-6
    assert nine["duels_total"] == 12 and nine["duels_won"] == 6
    assert nine["touches"] == 20 + 18 + 9
    assert nine["touches_per_match"] == round((20 + 18 + 9) / 3, 2)
    assert 0 <= nine["max_speed_mps"] <= 20


def test_team_stats_missing_team_is_404(client):
    assert client.get("/api/teams/999999/stats").status_code == 404


def test_team_stats_team_with_no_matches(client):
    s = _SessionLocal()
    lonely = Team(name="Lonely")
    s.add(lonely)
    s.commit()
    team_id = lonely.id
    s.close()

    body = client.get(f"/api/teams/{team_id}/stats").json()
    assert body["matches_played"] == 0
    assert body["avg_possession_pct"] is None
    assert body["trend"] == [] and body["players"] == []
