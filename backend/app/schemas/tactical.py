from pydantic import BaseModel


class TacticalPlayerRead(BaseModel):
    track_id: int
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