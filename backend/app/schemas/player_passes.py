from pydantic import BaseModel


class PassAttempt(BaseModel):
    timestamp: float
    sender_track_id: int | None
    recipient_track_id: int | None
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    distance_m: float
    length: str          # "short" | "long"
    completed: bool


class PlayerPassesRead(BaseModel):
    match_id: int
    match_player_id: int
    label: str
    team_role: str
    jersey_number: int | None
    passes: list[PassAttempt]
    total: int
    completed: int
    failed: int
    short: int
    long: int
    note: str
