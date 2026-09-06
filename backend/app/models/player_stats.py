from sqlalchemy import Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MatchPlayerStats(Base):
    """Advanced per-player counting stats aggregated on the whole-match identity.

    Everything here is derived from sparse detections and heuristic events:
    * passes/shots build on the existing Phase 4/5b/5d events;
    * duels and dribbles are brand-new, unvalidated heuristics (see
      ``app/services/player_stats.py``) - treat their numbers as a rough
      experimental signal, not a reliable metric.
    """

    __tablename__ = "match_player_stats"
    __table_args__ = (UniqueConstraint("match_id", "match_player_id", name="uq_match_player_stats"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(
        ForeignKey("matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    match_player_id: Mapped[int] = mapped_column(
        ForeignKey("match_players.id", ondelete="CASCADE"), nullable=False, index=True
    )

    passes_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passes_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passes_short: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passes_long: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    shots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    xg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    duels_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duels_won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    dribbles_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dribbles_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    match_player: Mapped["MatchPlayer"] = relationship()
