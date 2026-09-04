"""add detections table

Revision ID: 4c4d72f4d9a1
Revises: 00c11ff4fe88
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c4d72f4d9a1"
down_revision: Union[str, None] = "00c11ff4fe88"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "detections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("frame_timestamp", sa.Float(), nullable=False),
        sa.Column("bounding_box", sa.JSON(), nullable=False),
        sa.Column("class", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_detections_video_id", "detections", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_detections_video_id", table_name="detections")
    op.drop_table("detections")