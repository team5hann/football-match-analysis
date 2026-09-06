"""add per-frame pitch homography + projected detection coordinates

Revision ID: e5a7c9d1f3b8
Revises: d4f6a8c2e5b7
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a7c9d1f3b8"
down_revision: Union[str, None] = "d4f6a8c2e5b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("pitch_x", sa.Float(), nullable=True))
    op.add_column("detections", sa.Column("pitch_y", sa.Float(), nullable=True))

    op.create_table(
        "frame_homographies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("frame_timestamp", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="no_homography"),
        sa.Column("keypoint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matrix", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("video_id", "frame_timestamp", name="uq_frame_homography"),
    )
    op.create_index("ix_frame_homographies_video_id", "frame_homographies", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_frame_homographies_video_id", table_name="frame_homographies")
    op.drop_table("frame_homographies")
    op.drop_column("detections", "pitch_y")
    op.drop_column("detections", "pitch_x")
