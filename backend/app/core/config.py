from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg2://football:football@localhost:5432/football_analysis"

    video_storage_dir: Path = Path("./storage/videos")
    max_upload_size_bytes: int = 5 * 1024 * 1024 * 1024  # 5 GB

    cors_origins: str = "http://localhost:3000"

    ffprobe_binary: str = "ffprobe"
    ffmpeg_binary: str = "ffmpeg"
    detection_sample_interval_seconds: float = Field(default=0.033, gt=0)
    # Pitch homography is a heavy extra pass (it re-decodes frames and runs a
    # large pose model), and the camera pans slowly, so keypoints are sampled
    # much coarser than detections; in-between detections reuse the nearest
    # earlier frame's homography.
    homography_sample_interval_seconds: float = Field(default=0.5, gt=0)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.video_storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
