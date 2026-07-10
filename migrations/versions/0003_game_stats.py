"""game_stats — precomputed catalog-row stats (UI_PLAN.md §5.4)

Revision ID: 0003_game_stats
Revises: 0002_ui_indexes
Create Date: 2026-07-09

Adds the tiny ``game_stats`` table the catalog list joins so list pages never
aggregate ``signals_samples`` per row at request time (UI-M2). One row per game,
refreshed by the ``stats:refresh`` job every 15 min.

Sort-supporting indexes are ``DESC NULLS LAST`` to match the catalog's numeric
keyset ordering (players / 24h delta / reviews sorts).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003_game_stats"
down_revision: str | None = "0002_ui_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "game_stats",
        sa.Column(
            "game_id",
            sa.Integer(),
            sa.ForeignKey("games.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("current_players", sa.Float(), nullable=True),
        sa.Column("players_24h_delta", sa.Float(), nullable=True),
        sa.Column("players_7d_spark", JSONB(), nullable=False, server_default="[]"),
        sa.Column("review_count", sa.Float(), nullable=True),
        sa.Column("twitch_viewers", sa.Float(), nullable=True),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # DESC NULLS LAST to make the catalog's numeric sorts index-only ordered.
    op.execute("CREATE INDEX ix_game_stats_players ON game_stats (current_players DESC NULLS LAST)")
    op.execute("CREATE INDEX ix_game_stats_delta ON game_stats (players_24h_delta DESC NULLS LAST)")
    op.execute("CREATE INDEX ix_game_stats_reviews ON game_stats (review_count DESC NULLS LAST)")
    # GIN on games.genres for the catalog's JSONB containment filter
    # (``genres @> '["X"]'``) so genre chips don't seq-scan the catalog.
    op.execute("CREATE INDEX ix_games_genres_gin ON games USING gin (genres jsonb_path_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_games_genres_gin")
    op.execute("DROP INDEX IF EXISTS ix_game_stats_reviews")
    op.execute("DROP INDEX IF EXISTS ix_game_stats_delta")
    op.execute("DROP INDEX IF EXISTS ix_game_stats_players")
    op.drop_table("game_stats")
