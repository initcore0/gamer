"""streamer_prefs.subscribed_genres — genre subscriptions (M7)

Revision ID: 0005_subscribed_genres
Revises: 0004_game_embedding_hnsw
Create Date: 2026-07-09

Adds ``streamer_prefs.subscribed_genres`` (JSONB, NOT NULL, server_default ``[]``):
the streamer's hard "always cover this genre" set (GENRE_SUBS_PLAN.md). Distinct
from ``liked_genres`` (soft taste). Drives the genre-track job, the ``genre_sub``
score component, and the digest quota.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005_subscribed_genres"
down_revision: str | None = "0004_game_embedding_hnsw"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "streamer_prefs",
        sa.Column(
            "subscribed_genres",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("streamer_prefs", "subscribed_genres")
