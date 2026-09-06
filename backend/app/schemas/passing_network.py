from pydantic import BaseModel


class PassingNetworkNode(BaseModel):
    track_id: int
    match_player_id: int | None = None
    team_role: str
    jersey_number: int | None
    label: str
    average_x: float
    average_y: float
    detections_count: int


class PassingNetworkEdge(BaseModel):
    source_track_id: int
    target_track_id: int
    pass_count: int
    source_x: float
    source_y: float
    target_x: float
    target_y: float


class PassingNetworkTeam(BaseModel):
    role: str
    nodes: list[PassingNetworkNode]
    edges: list[PassingNetworkEdge]


class PassingNetworkRead(BaseModel):
    home: PassingNetworkTeam
    away: PassingNetworkTeam
    coordinate_note: str