"""add phase 4 tracking and analysis fields

Revision ID: 9b18d3e7f5a1
Revises: 8a12f7c4e6b2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9b18d3e7f5a1"
down_revision: Union[str, None] = "8a12f7c4e6b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("track_id", sa.Integer(), nullable=True))
    op.create_index("ix_detections_track_id", "detections", ["track_id"])
    op.add_column("events", sa.Column("track_id", sa.Integer(), nullable=True))
    op.create_table(
        "match_analysis_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("home_possession_pct", sa.Float(), nullable=False),
        sa.Column("away_possession_pct", sa.Float(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id"),
    )
    op.create_table(
        "player_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("team_color_cluster", sa.Integer(), nullable=True),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("touches", sa.Integer(), nullable=False),
        sa.Column("distance_meters", sa.Float(), nullable=False),
        sa.Column("average_speed_mps", sa.Float(), nullable=False),
        sa.Column("max_speed_mps", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_player_metrics_match_id", "player_metrics", ["match_id"])


def downgrade() -> None:
    op.drop_index("ix_player_metrics_match_id", table_name="player_metrics")
    op.drop_table("player_metrics")
    op.drop_table("match_analysis_summaries")
    op.drop_column("events", "track_id")
    op.drop_index("ix_detections_track_id", table_name="detections")
    op.drop_column("detections", "track_id")