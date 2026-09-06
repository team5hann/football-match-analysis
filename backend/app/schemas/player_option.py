from pydantic import BaseModel


class PlayerOptionRead(BaseModel):
    track_id: int
    match_player_id: int | None = None
    team_color_cluster: int | None
    team_role: str
    jersey_number: int | None
    label: str
    detection_count: int