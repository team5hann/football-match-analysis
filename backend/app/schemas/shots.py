from pydantic import BaseModel


class ShotRead(BaseModel):
    id: int
    timestamp_seconds: float
    track_id: int | None
    team_role: str
    xg: float
    position_x: float | None
    position_y: float | None
    description: str | None


class ShotsRead(BaseModel):
    status: str
    home_xg: float
    away_xg: float
    shots: list[ShotRead]
    note: str