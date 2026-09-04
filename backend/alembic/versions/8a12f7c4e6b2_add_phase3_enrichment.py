"""add phase 3 color clusters, OCR fields, and team mappings

Revision ID: 8a12f7c4e6b2
Revises: 4c4d72f4d9a1
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8a12f7c4e6b2"
down_revision: Union[str, None] = "4c4d72f4d9a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("team_color_cluster", sa.Integer(), nullable=True))
    op.add_column("detections", sa.Column("dominant_rgb", sa.JSON(), nullable=True))
    op.add_column("detections", sa.Column("jersey_number", sa.Integer(), nullable=True))
    op.add_column("detections", sa.Column("jersey_number_confidence", sa.Float(), nullable=True))
    op.create_table(
        "team_cluster_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "cluster_id", name="uq_match_cluster"),
    )
    op.create_index("ix_team_cluster_assignments_match_id", "team_cluster_assignments", ["match_id"])


def downgrade() -> None:
    op.drop_index("ix_team_cluster_assignments_match_id", table_name="team_cluster_assignments")
    op.drop_table("team_cluster_assignments")
    op.drop_column("detections", "jersey_number_confidence")
    op.drop_column("detections", "jersey_number")
    op.drop_column("detections", "dominant_rgb")
    op.drop_column("detections", "team_color_cluster")