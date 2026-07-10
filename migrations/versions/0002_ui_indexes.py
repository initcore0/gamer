"""ui indexes — pg_trgm name search + catalog/news list indexes

Revision ID: 0002_ui_indexes
Revises: 0001_initial
Create Date: 2026-07-09

Adds the indexes the read-only web UI needs for fast list/search pages
(UI_PLAN.md §5). Hand-authored (op.execute for the ones alembic can't express):

* pg_trgm extension + GIN trigram index on ``lower(games.name)`` — turns fuzzy
  ``ILIKE '%q%'`` name search into a millisecond index scan.
* partial index on ``games (tracked) WHERE tracked`` — the tracked-only filter.
* ``games (platform, release_date DESC)`` — common filter/sort combo.
* ``news_items (published_at DESC)`` and ``(cluster_id, published_at)`` — the
  news stream (UI-M4) and cluster grouping.

HNSW / pgvector indexes are intentionally deferred to UI-M3.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_ui_indexes"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX ix_games_name_trgm ON games USING gin (lower(name) gin_trgm_ops)")
    op.execute("CREATE INDEX ix_games_tracked ON games (tracked) WHERE tracked")
    op.execute("CREATE INDEX ix_games_platform_release ON games (platform, release_date DESC)")
    op.execute("CREATE INDEX ix_news_published ON news_items (published_at DESC)")
    op.execute("CREATE INDEX ix_news_cluster_published ON news_items (cluster_id, published_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_news_cluster_published")
    op.execute("DROP INDEX IF EXISTS ix_news_published")
    op.execute("DROP INDEX IF EXISTS ix_games_platform_release")
    op.execute("DROP INDEX IF EXISTS ix_games_tracked")
    op.execute("DROP INDEX IF EXISTS ix_games_name_trgm")
    # Leave the pg_trgm extension in place — other objects may depend on it.
