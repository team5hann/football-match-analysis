from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg2://football:football@localhost:5432/football_analysis"

    video_storage_dir: Path = Path("./storage/videos")
    max_upload_size_bytes: int = 5 * 1024 * 1024 * 1024  # 5 GB

    cors_origins: str = "http://localhost:3000"

    ffprobe_binary: str = "ffprobe"
    ffmpeg_binary: str = "ffmpeg"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.video_storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
