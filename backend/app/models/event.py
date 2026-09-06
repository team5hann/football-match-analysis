from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Event(Base):
    """A detected (or manually annotated) game event, timestamped within a match video.

    Left unpopulated in Phase 1 - the AI event detection pipeline (Phase 4+) will
    write rows here. The schema is defined now so downstream phases don't need
    a migration to introduce it.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), nullable=False)
    video_id: Mapped[int | None] = mapped_column(ForeignKey("videos.id", ondelete="SET NULL"), nullable=True)
    player_id: Mapped[int | None] = mapped_column(ForeignKey("players.id", ondelete="SET NULL"), nullable=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Second player involved, for events that are inherently about two players
    # (duel: winner=track_id, loser=related_track_id; dribble: track_id=carrier,
    # related_track_id=nearest opponent).
    related_track_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Free-form result marker, e.g. "successful"/"unsuccessful" for dribbles.
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    # 2D pitch coordinates (0-100 normalized), populated once pitch homography exists
    position_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_y: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    manually_verified: Mapped[bool] = mapped_column(default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    match: Mapped["Match"] = relationship(back_populates="events")
