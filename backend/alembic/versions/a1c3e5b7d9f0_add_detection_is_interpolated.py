"""flag ball detections filled in by trajectory interpolation

Revision ID: a1c3e5b7d9f0
Revises: f6b8d2e4a7c9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c3e5b7d9f0"
down_revision: Union[str, None] = "f6b8d2e4a7c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "detections",
        sa.Column("is_interpolated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("detections", "is_interpolated")
