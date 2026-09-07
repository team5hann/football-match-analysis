"""Cross-match / season aggregation for a single team.

Two aggregations:

* **Team form** - one row per match the team played (home or away), plus totals
  and averages: matches played, average possession, total/average xG, and
  win/draw/loss + goals when ``home_score``/``away_score`` are filled in.

* **Cross-match players** - grouped by ``(team_id, jersey_number)``. This is a
  deliberately loose heuristic: the same shirt number in the same team across
  matches is *probably* the same player, but squads change between games,
  numbers get reassigned, and a number can be worn by a substitute. It is NOT a
  reliable identity - treat these rows as "shirt #N for this team, summed over
  the games we have", not "player X".
"""
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.analysis import MatchAnalysisSummary, PlayerMetric
from app.models.match import Match
from app.models.player_identity import MatchPlayer
from app.models.player_stats import MatchPlayerStats
from app.models.team import Team

# match_player_stats fields that are plain sums across matches.
_SUM_FIELDS = (
    "passes_total",
    "passes_completed",
    "passes_short",
    "passes_long",
    "passes_short_completed",
    "passes_long_completed",
    "shots",
    "xg",
    "duels_total",
    "duels_won",
    "dribbles_total",
    "dribbles_completed",
)


def _team_cluster_ids(match: Match, team_id: int, side: str) -> set[int]:
    """Team-colour clusters that belong to ``team_id`` in ``match``.

    Prefers the explicit ``team_id`` on the cluster assignment; falls back to the
    home/away role when the assignment was left unlinked to a team.
    """
    explicit = {a.cluster_id for a in match.team_cluster_assignments if a.team_id == team_id}
    if explicit:
        return explicit
    return {a.cluster_id for a in match.team_cluster_assignments if a.team_id is None and a.role == side}


def _match_result(match: Match, side: str) -> str | None:
    if match.home_score is None or match.away_score is None:
        return None
    ours, theirs = (
        (match.home_score, match.away_score) if side == "home" else (match.away_score, match.home_score)
    )
    if ours > theirs:
        return "win"
    if ours < theirs:
        return "loss"
    return "draw"


def _identity_stats_rows(db: Session, match: Match, cluster_ids: set[int]):
    """(MatchPlayer, MatchPlayerStats|None, base_metrics_dict) for the team in one match."""
    if not cluster_ids:
        return []
    identities = db.scalars(
        select(MatchPlayer).where(
            MatchPlayer.match_id == match.id,
            MatchPlayer.team_color_cluster.in_(cluster_ids),
        )
    ).all()
    if not identities:
        return []
    by_id = {identity.id: identity for identity in identities}
    stats_by_identity = {
        stat.match_player_id: stat
        for stat in db.scalars(
            select(MatchPlayerStats).where(MatchPlayerStats.match_player_id.in_(by_id))
        ).all()
    }
    base_by_identity: dict[int, dict] = defaultdict(
        lambda: {"touches": 0, "distance_meters": 0.0, "max_speeds": []}
    )
    for metric in db.scalars(
        select(PlayerMetric).where(PlayerMetric.match_player_id.in_(by_id))
    ).all():
        bucket = base_by_identity[metric.match_player_id]
        bucket["touches"] += metric.touches
        bucket["distance_meters"] += metric.distance_meters
        bucket["max_speeds"].append(metric.max_speed_mps)
    return [
        (identity, stats_by_identity.get(identity.id), base_by_identity.get(identity.id))
        for identity in identities
    ]


def aggregate_team_stats(db: Session, team_id: int) -> dict:
    team = db.get(Team, team_id)
    if team is None:
        raise ValueError("Team not found")

    matches = db.scalars(
        select(Match)
        .where(or_(Match.home_team_id == team_id, Match.away_team_id == team_id))
        .order_by(Match.match_date.is_(None), Match.match_date, Match.id)
    ).all()

    trend: list[dict] = []
    possession_values: list[float] = []
    total_xg = 0.0
    total_shots = 0
    results = {"win": 0, "draw": 0, "loss": 0}
    goals_for = goals_against = results_recorded = 0

    # cross-match player accumulation, keyed by jersey number
    players_by_number: dict[int, dict] = {}
    identities_without_number = 0

    for match in matches:
        side = "home" if match.home_team_id == team_id else "away"
        opponent_id = match.away_team_id if side == "home" else match.home_team_id
        opponent = db.get(Team, opponent_id) if opponent_id else None
        cluster_ids = _team_cluster_ids(match, team_id, side)

        summary = db.scalar(
            select(MatchAnalysisSummary).where(MatchAnalysisSummary.match_id == match.id)
        )
        possession = None
        if summary is not None:
            possession = round(
                summary.home_possession_pct if side == "home" else summary.away_possession_pct, 2
            )
            if possession:
                possession_values.append(possession)

        rows = _identity_stats_rows(db, match, cluster_ids)
        match_xg = round(sum(stat.xg for _i, stat, _b in rows if stat), 4)
        match_shots = sum(stat.shots for _i, stat, _b in rows if stat)
        total_xg += match_xg
        total_shots += match_shots

        result = _match_result(match, side)
        if result is not None:
            results[result] += 1
            results_recorded += 1
            our_goals = match.home_score if side == "home" else match.away_score
            their_goals = match.away_score if side == "home" else match.home_score
            goals_for += our_goals
            goals_against += their_goals

        trend.append(
            {
                "match_id": match.id,
                "match_date": match.match_date,
                "opponent_team_id": opponent_id,
                "opponent_name": opponent.name if opponent else None,
                "side": side,
                "status": match.status.value if hasattr(match.status, "value") else match.status,
                "possession_pct": possession,
                "xg": match_xg,
                "shots": match_shots,
                "result": result,
                "goals_for": match.home_score if side == "home" else match.away_score,
                "goals_against": match.away_score if side == "home" else match.home_score,
            }
        )

        for identity, stat, base in rows:
            if identity.jersey_number is None:
                identities_without_number += 1
                continue
            bucket = players_by_number.setdefault(
                identity.jersey_number,
                {"jersey_number": identity.jersey_number, "matches": set(), "touches": 0,
                 "distance_meters": 0.0, "max_speed_mps": 0.0,
                 **{field: 0 for field in _SUM_FIELDS}},
            )
            bucket["matches"].add(match.id)
            if base:
                bucket["touches"] += base["touches"]
                bucket["distance_meters"] += base["distance_meters"]
                bucket["max_speed_mps"] = max(bucket["max_speed_mps"], *(base["max_speeds"] or [0.0]))
            if stat:
                for field in _SUM_FIELDS:
                    bucket[field] += getattr(stat, field, 0) or 0

    matches_played = len(matches)
    matches_with_analysis = sum(1 for row in trend if row["possession_pct"] is not None)

    players = []
    for number in sorted(players_by_number):
        bucket = players_by_number[number]
        appearances = len(bucket["matches"])
        players.append(
            {
                "jersey_number": number,
                "matches_played": appearances,
                "touches": bucket["touches"],
                "touches_per_match": round(bucket["touches"] / appearances, 2) if appearances else 0.0,
                "distance_meters": round(bucket["distance_meters"], 1),
                "max_speed_mps": round(bucket["max_speed_mps"], 2),
                "passes_total": bucket["passes_total"],
                "passes_completed": bucket["passes_completed"],
                "passes_short": bucket["passes_short"],
                "passes_long": bucket["passes_long"],
                "passes_short_completed": bucket["passes_short_completed"],
                "passes_long_completed": bucket["passes_long_completed"],
                "shots": bucket["shots"],
                "xg": round(bucket["xg"], 4),
                "duels_total": bucket["duels_total"],
                "duels_won": bucket["duels_won"],
                "dribbles_total": bucket["dribbles_total"],
                "dribbles_completed": bucket["dribbles_completed"],
            }
        )

    return {
        "team_id": team_id,
        "team_name": team.name,
        "matches_played": matches_played,
        "matches_with_analysis": matches_with_analysis,
        "avg_possession_pct": round(sum(possession_values) / len(possession_values), 2)
        if possession_values
        else None,
        "total_xg": round(total_xg, 4),
        "avg_xg": round(total_xg / matches_played, 4) if matches_played else 0.0,
        "total_shots": total_shots,
        "results_recorded": results_recorded,
        "wins": results["win"],
        "draws": results["draw"],
        "losses": results["loss"],
        "goals_for": goals_for,
        "goals_against": goals_against,
        "trend": trend,
        "players": players,
        "players_without_jersey_number": identities_without_number,
        "player_identity_note": (
            "Players are grouped by (team, shirt number) across matches - a rough "
            "heuristic, not a real identity: squad numbers get reassigned between "
            "games and substitutes can share a number."
        ),
    }
