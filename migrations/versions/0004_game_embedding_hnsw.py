"""game embedding column + HNSW pgvector indexes (UI_PLAN.md §5.1, §8 UI-M3)

Revision ID: 0004_game_embedding_hnsw
Revises: 0003_game_stats
Create Date: 2026-07-09

UI-M3 adds the similar-games feature (pgvector cosine nearest neighbours). Games
had no stored vector, so this migration adds ``games.embedding vector(384)`` and
an HNSW index (``vector_cosine_ops``) over it. The vector is backfilled at
request-cadence by the ``stats:refresh`` job (embeds games missing an embedding,
batch ≤200/run, via ``game_text`` + ``get_embedder``), and ``similar_games`` is a
single ``ORDER BY embedding <=> :vec LIMIT k`` scan over the non-null rows.

Also lands the deferred HNSW index on ``news_items.embedding`` (UI-M2 §5.1
leftover) so cluster/dedup neighbour queries hit an index rather than seq-scanning.

Both HNSW indexes use ``vector_cosine_ops`` to match the ``<=>`` cosine-distance
operator used by :mod:`gamer.enrichment.embeddings` and ``queries.game_detail``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0004_game_embedding_hnsw"
down_revision: str | None = "0003_game_stats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMBEDDING_DIM = 384


def upgrade() -> None:
    op.add_column("games", sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True))
    # HNSW cosine index for similar-games nearest-neighbour scans over the games
    # with a backfilled embedding (partial: NULL rows carry no vector to index).
    op.execute(
        "CREATE INDEX ix_games_embedding_hnsw ON games USING hnsw (embedding vector_cosine_ops)"
    )
    # Deferred UI-M2 leftover: HNSW on news embeddings for dedup/cluster queries.
    op.execute(
        "CREATE INDEX ix_news_embedding_hnsw ON news_items USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_news_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_games_embedding_hnsw")
    op.drop_column("games", "embedding")
