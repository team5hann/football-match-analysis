from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MatchPlayer(Base):
    """A best-effort, whole-match player identity.

    Built by stitching together short tracking segments (``track_id``) that
    share the same team colour cluster and the same confidently-read jersey
    number. This is a colour + number heuristic, NOT visual re-identification:
    segments without a confident number cannot be linked and each stay their
    own ``is_unknown`` identity.
    """

    __tablename__ = "match_players"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_color_cluster: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jersey_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # True when no confident jersey number was available, so the identity is a
    # single unmerged track kept separate on purpose.
    is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    match: Mapped["Match"] = relationship()
    track_links: Mapped[list["TrackPlayerLink"]] = relationship(
        back_populates="match_player", cascade="all, delete-orphan"
    )


class TrackPlayerLink(Base):
    """Maps one tracking segment (``track_id`` within a match) to a MatchPlayer."""

    __tablename__ = "track_player_links"
    __table_args__ = (UniqueConstraint("match_id", "track_id", name="uq_match_track_player"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    match_player_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE"), nullable=False, index=True
    )

    match_player: Mapped["MatchPlayer"] = relationship(back_populates="track_links")
