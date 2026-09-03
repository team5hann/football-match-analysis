from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import VideoStatus


class VideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    match_id: int
    original_filename: str
    stored_filename: str = Field(exclude=True)
    file_size_bytes: int | None
    content_type: str | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    video_codec: str | None
    audio_codec: str | None
    bitrate: int | None
    status: VideoStatus
    error_message: str | None
    uploaded_at: datetime

    @computed_field
    @property
    def stream_url(self) -> str:
        return f"/media/{self.stored_filename}"
