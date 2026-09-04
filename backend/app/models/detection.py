from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Detection(Base):
    __tablename__ = "detections"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)
    frame_timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    bounding_box: Mapped[dict] = mapped_column(JSON, nullable=False)
    class_name: Mapped[str] = mapped_column("class", String(20), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    video: Mapped["Video"] = relationship(back_populates="detections")