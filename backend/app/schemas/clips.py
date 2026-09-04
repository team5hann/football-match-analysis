from pydantic import BaseModel


class ClipEventRead(BaseModel):
    event_id: int
    event_type: str
    timestamp_seconds: float
    xg: float | None
    confidence: float | None
    track_id: int | None
    description: str | None