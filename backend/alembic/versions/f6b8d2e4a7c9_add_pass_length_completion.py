"""add short/long completed pass counts to match_player_stats

Revision ID: f6b8d2e4a7c9
Revises: e5a7c9d1f3b8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6b8d2e4a7c9"
down_revision: Union[str, None] = "e5a7c9d1f3b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "match_player_stats",
        sa.Column("passes_short_completed", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "match_player_stats",
        sa.Column("passes_long_completed", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("match_player_stats", "passes_long_completed")
    op.drop_column("match_player_stats", "passes_short_completed")
