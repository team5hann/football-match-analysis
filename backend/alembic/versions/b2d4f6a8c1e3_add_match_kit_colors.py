"""add match kit colors and cluster assignment metadata

Revision ID: b2d4f6a8c1e3
Revises: a7c9e1d5f2b4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4f6a8c1e3"
down_revision: Union[str, None] = "a7c9e1d5f2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("home_team_color", sa.String(length=7), nullable=True))
    op.add_column("matches", sa.Column("away_team_color", sa.String(length=7), nullable=True))
    op.add_column(
        "team_cluster_assignments",
        sa.Column("assignment_source", sa.String(length=20), nullable=False, server_default="manual"),
    )
    op.add_column("team_cluster_assignments", sa.Column("similarity", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("team_cluster_assignments", "similarity")
    op.drop_column("team_cluster_assignments", "assignment_source")
    op.drop_column("matches", "away_team_color")
    op.drop_column("matches", "home_team_color")