from pydantic import BaseModel, Field


class HeatmapRead(BaseModel):
    mode: str
    track_id: int | None = None
    match_player_id: int | None = None
    team_color_cluster: int | None = None
    grid_width: int = Field(ge=1)
    grid_height: int = Field(ge=1)
    grid: list[list[int]]
    total_observations: int
    coordinate_note: str