from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DetectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    video_id: int
    frame_timestamp: float
    bounding_box: dict[str, float]
    class_name: str = Field(alias="class")
    confidence: float
    track_id: int | None = None
    team_color_cluster: int | None = None
    dominant_rgb: list[int] | None = None
    jersey_number: int | None = None
    jersey_number_confidence: float | None = None
    created_at: datetime


class DetectionStatus(BaseModel):
    video_id: int
    status: str
    detections_count: int
    detections: list[DetectionRead]


class DetectionUpdate(BaseModel):
    jersey_number: int | None = Field(default=None, ge=0, le=99)