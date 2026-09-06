from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MatchAnalysisSummary(Base):
    __tablename__ = "match_analysis_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), unique=True)
    home_possession_pct: Mapped[float] = mapped_column(Float, default=0)
    away_possession_pct: Mapped[float] = mapped_column(Float, default=0)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    match: Mapped["Match"] = relationship(back_populates="analysis_summary")


class PlayerMetric(Base):
    __tablename__ = "player_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Whole-match player identity this metric row belongs to, when the identity
    # merge (player_identity service) has been run for the match.
    match_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("match_players.id", ondelete="SET NULL"), nullable=True, index=True
    )
    team_color_cluster: Mapped[int | None] = mapped_column(nullable=True)
    jersey_number: Mapped[int | None] = mapped_column(nullable=True)
    touches: Mapped[int] = mapped_column(Integer, default=0)
    distance_meters: Mapped[float] = mapped_column(Float, default=0)
    average_speed_mps: Mapped[float] = mapped_column(Float, default=0)
    max_speed_mps: Mapped[float] = mapped_column(Float, default=0)

    match: Mapped["Match"] = relationship(back_populates="player_metrics")