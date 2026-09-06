from pydantic import BaseModel


class HomographyStatus(BaseModel):
    video_id: int
    frames_processed: int
    frames_with_homography: int
    frames_without_homography: int
    detections_projected: int
    detections_total: int


class HomographyRunStatus(HomographyStatus):
    sample_interval_seconds: float
    note: str
