from pydantic import BaseModel


class MatchPlayerStatRead(BaseModel):
    match_player_id: int
    team_role: str
    jersey_number: int | None
    is_unknown: bool
    label: str

    # base movement metrics, folded onto the identity
    touches: int
    distance_meters: float
    average_speed_mps: float
    max_speed_mps: float

    # passes
    passes_total: int
    passes_completed: int
    passes_short: int
    passes_long: int

    # shots
    shots: int
    xg: float

    # duels / dribbles (experimental heuristics)
    duels_total: int
    duels_won: int
    dribbles_total: int
    dribbles_completed: int


class PlayerStatsRead(BaseModel):
    match_id: int
    players: list[MatchPlayerStatRead]


class PlayerStatsRunRead(PlayerStatsRead):
    identities_with_stats: int
    unlinked_tracks: int
    duels_detected: int
    dribbles_detected: int
    dribbles_successful: int
    passes_total: int
    passes_completed: int
    passes_short: int
    passes_long: int
    shots_assigned: int
    note: str
