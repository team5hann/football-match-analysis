"""add advanced player stats (passes/shots/duels/dribbles) + event links

Revision ID: d4f6a8c2e5b7
Revises: c3e5a7b9d1f2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4f6a8c2e5b7"
down_revision: Union[str, None] = "c3e5a7b9d1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("related_track_id", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("outcome", sa.String(length=20), nullable=True))

    op.create_table(
        "match_player_stats",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("match_player_id", sa.Integer(), nullable=False),
        sa.Column("passes_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passes_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passes_short", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passes_long", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shots", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("xg", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duels_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duels_won", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dribbles_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dribbles_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_player_id"], ["match_players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "match_player_id", name="uq_match_player_stats"),
    )
    op.create_index("ix_match_player_stats_match_id", "match_player_stats", ["match_id"])
    op.create_index("ix_match_player_stats_match_player_id", "match_player_stats", ["match_player_id"])


def downgrade() -> None:
    op.drop_index("ix_match_player_stats_match_player_id", table_name="match_player_stats")
    op.drop_index("ix_match_player_stats_match_id", table_name="match_player_stats")
    op.drop_table("match_player_stats")
    op.drop_column("events", "outcome")
    op.drop_column("events", "related_track_id")
