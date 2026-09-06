from pydantic import BaseModel


class PlayerMetricRead(BaseModel):
    track_id: int
    match_player_id: int | None = None
    team_color_cluster: int | None
    jersey_number: int | None
    touches: int
    distance_meters: float
    average_speed_mps: float
    max_speed_mps: float


class AnalysisRead(BaseModel):
    status: str
    home_possession_pct: float
    away_possession_pct: float
    players: list[PlayerMetricRead]
    events: list[dict]