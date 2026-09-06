from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class FrameHomography(Base):
    """Per-frame image->pitch homography for a video.

    Veo "Follow-cam" exports are a digitally cropped/zoomed window over a fixed
    panorama, so the effective camera pans and zooms. One homography for the
    whole clip is therefore wrong - each sampled frame gets its own, computed
    from that frame's detected pitch keypoints. Frames whose keypoints are too
    few for a stable fit are stored with ``status='no_homography'`` and are
    skipped by the spatial calculations (they fall back to the old scaling).
    """

    __tablename__ = "frame_homographies"
    __table_args__ = (UniqueConstraint("video_id", "frame_timestamp", name="uq_frame_homography"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(
        ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    frame_timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="no_homography")
    keypoint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 3x3 row-major matrix mapping pixel (x, y, 1) -> pitch metres (105 x 68),
    # null when status != "ok".
    matrix: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    video: Mapped["Video"] = relationship()
