from pydantic import BaseModel


class TacticalPlayerRead(BaseModel):
    track_id: int
    match_player_id: int | None = None
    jersey_number: int | None
    label: str
    average_x: float
    average_y: float
    detections_count: int


class TacticalRead(BaseModel):
    team_role: str
    formation: str
    width: float
    depth: float
    compactness: float
    players: list[TacticalPlayerRead]
    coordinate_note: str