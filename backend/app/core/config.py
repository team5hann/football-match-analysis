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
    # Player detector. The medium YOLO11 model is far better than yolov8n at
    # small, distant and partially occluded players; set PLAYER_MODEL=yolo11s.pt
    # (faster) or yolo11l.pt / yolo11x.pt (even more accurate) to trade off.
    player_model: str = "yolo11m.pt"
    # Inference resolution. 1280 instead of the default 640 keeps far-side
    # players and the ball several pixels larger on a 1080p+ match camera.
    player_imgsz: int = Field(default=1280, ge=320)
    ball_imgsz: int = Field(default=1280, ge=320)
    # Low on purpose: a blurred ball in flight scores low, and the trajectory
    # filter (services/ball_tracking.py) removes the resulting false positives.
    ball_confidence: float = Field(default=0.05, gt=0, lt=1)
    # Ball is also detected on a grid x grid of overlapping tiles (2 -> 4
    # tiles + the full frame, i.e. 5 model runs per frame). 1 disables tiling.
    ball_tile_grid: int = Field(default=2, ge=1, le=4)
    # Longest gap (detector missed the ball) that is filled by interpolation.
    ball_max_gap_seconds: float = Field(default=0.5, ge=0)
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
