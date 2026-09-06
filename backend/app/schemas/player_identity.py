from pydantic import BaseModel


class MatchPlayerRead(BaseModel):
    id: int
    team_color_cluster: int | None
    team_role: str
    jersey_number: int | None
    confidence: float
    is_unknown: bool
    label: str
    track_ids: list[int]
    detection_count: int


class PlayerIdentitiesRead(BaseModel):
    match_id: int
    track_count: int
    identified_count: int
    unknown_count: int
    players: list[MatchPlayerRead]
    note: str = (
        "Heuristic stitching by team colour + confidently-read jersey number, "
        "not visual re-identification. Tracks without a confident number stay "
        "separate as 'Unknown #<track_id>'."
    )
