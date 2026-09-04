from pydantic import BaseModel


class PlayerOptionRead(BaseModel):
    track_id: int
    team_color_cluster: int | None
    team_role: str
    jersey_number: int | None
    label: str
    detection_count: int