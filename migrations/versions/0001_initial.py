"""initial schema — catalog, signals, news, recommendations, outbox, state

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-09

Hand-authored to match gamer.db.models. Enables the pgvector extension first.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "games",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False, server_default="steam"),
        sa.Column("platform_app_id", sa.BigInteger, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("genres", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("release_date", sa.DateTime(timezone=True)),
        sa.Column("price_cents", sa.Integer),
        sa.Column("is_free", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("details_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("tracked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("platform", "platform_app_id", name="uq_game_platform_app"),
    )

    op.create_table(
        "game_tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "game_id", sa.Integer, sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("tag", sa.String(128), nullable=False),
        sa.Column("weight", sa.Integer),
        sa.UniqueConstraint("game_id", "tag", name="uq_game_tag"),
    )
    op.create_index("ix_game_tags_game_id", "game_tags", ["game_id"])

    op.create_table(
        "signals_samples",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "game_id", sa.Integer, sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.UniqueConstraint("game_id", "metric", "ts", name="uq_sample"),
    )
    op.create_index("ix_sample_game_metric_ts", "signals_samples", ["game_id", "metric", "ts"])

    op.create_table(
        "signals_rollups",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "game_id", sa.Integer, sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("metric", sa.String(32), nullable=False),
        sa.Column("window", sa.String(8), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("avg", sa.Float, nullable=False),
        sa.Column("min", sa.Float, nullable=False),
        sa.Column("max", sa.Float, nullable=False),
        sa.Column("samples", sa.Integer, nullable=False),
        sa.UniqueConstraint("game_id", "metric", "window", "bucket_start", name="uq_rollup"),
    )
    op.create_index(
        "ix_rollup_game_metric_bucket", "signals_rollups", ["game_id", "metric", "bucket_start"]
    )

    op.create_table(
        "news_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("game_id", sa.Integer, sa.ForeignKey("games.id", ondelete="SET NULL")),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text),
        sa.Column("body", sa.Text),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("cluster_id", sa.Integer),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source", "external_id", name="uq_news_natural_key"),
    )
    op.create_index("ix_news_game_published", "news_items", ["game_id", "published_at"])
    op.create_index("ix_news_items_cluster_id", "news_items", ["cluster_id"])

    op.create_table(
        "recommendations",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "game_id", sa.Integer, sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("breakdown", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_rec_game_created", "recommendations", ["game_id", "created_at"])

    op.create_table(
        "feedback",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "rec_id",
            sa.BigInteger,
            sa.ForeignKey("recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "streamer_prefs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(64), nullable=False, server_default="default"),
        sa.Column("liked_genres", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("blocked_genres", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("muted_game_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("digest_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("profile_embedding", Vector(EMBEDDING_DIM)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("key", name="uq_streamer_prefs_key"),
    )

    op.create_table(
        "outbox",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("dedup_key", sa.String(256), nullable=False),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("message_id", sa.String(128)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("channel", "dedup_key", name="uq_outbox_dedup"),
    )
    op.create_index("ix_outbox_status", "outbox", ["status"])

    op.create_table(
        "source_cursors",
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("cursor", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("detail", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text),
    )
    op.create_index("ix_jobs_name_started", "jobs", ["name", "started_at"])


def downgrade() -> None:
    for table in (
        "jobs",
        "source_cursors",
        "outbox",
        "streamer_prefs",
        "feedback",
        "recommendations",
        "news_items",
        "signals_rollups",
        "signals_samples",
        "game_tags",
        "games",
    ):
        op.drop_table(table)
