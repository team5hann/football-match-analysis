from datetime import datetime

from pydantic import BaseModel


class TeamMatchTrendRow(BaseModel):
    match_id: int
    match_date: datetime | None
    opponent_team_id: int | None
    opponent_name: str | None
    side: str
    status: str
    possession_pct: float | None
    xg: float
    shots: int
    result: str | None
    goals_for: int | None
    goals_against: int | None


class TeamPlayerAggregate(BaseModel):
    jersey_number: int
    matches_played: int
    touches: int
    touches_per_match: float
    distance_meters: float
    max_speed_mps: float
    passes_total: int
    passes_completed: int
    passes_short: int
    passes_long: int
    passes_short_completed: int
    passes_long_completed: int
    shots: int
    xg: float
    duels_total: int
    duels_won: int
    dribbles_total: int
    dribbles_completed: int


class TeamStatsRead(BaseModel):
    team_id: int
    team_name: str
    matches_played: int
    matches_with_analysis: int
    avg_possession_pct: float | None
    total_xg: float
    avg_xg: float
    total_shots: int
    results_recorded: int
    wins: int
    draws: int
    losses: int
    goals_for: int
    goals_against: int
    trend: list[TeamMatchTrendRow]
    players: list[TeamPlayerAggregate]
    players_without_jersey_number: int
    player_identity_note: str
