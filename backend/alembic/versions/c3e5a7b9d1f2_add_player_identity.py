"""add player identity layer (match_players, track_player_links)

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c1e3
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3e5a7b9d1f2"
down_revision: Union[str, None] = "b2d4f6a8c1e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "match_players",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("team_color_cluster", sa.Integer(), nullable=True),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_unknown", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_match_players_match_id", "match_players", ["match_id"])

    op.create_table(
        "track_player_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("match_player_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["match_id"], ["matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["match_player_id"], ["match_players.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("match_id", "track_id", name="uq_match_track_player"),
    )
    op.create_index("ix_track_player_links_match_id", "track_player_links", ["match_id"])
    op.create_index("ix_track_player_links_match_player_id", "track_player_links", ["match_player_id"])

    op.add_column("player_metrics", sa.Column("match_player_id", sa.Integer(), nullable=True))
    op.create_index("ix_player_metrics_match_player_id", "player_metrics", ["match_player_id"])
    op.create_foreign_key(
        "fk_player_metrics_match_player_id",
        "player_metrics",
        "match_players",
        ["match_player_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_player_metrics_match_player_id", "player_metrics", type_="foreignkey")
    op.drop_index("ix_player_metrics_match_player_id", table_name="player_metrics")
    op.drop_column("player_metrics", "match_player_id")

    op.drop_index("ix_track_player_links_match_player_id", table_name="track_player_links")
    op.drop_index("ix_track_player_links_match_id", table_name="track_player_links")
    op.drop_table("track_player_links")

    op.drop_index("ix_match_players_match_id", table_name="match_players")
    op.drop_table("match_players")
