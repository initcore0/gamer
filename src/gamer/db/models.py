"""ORM models — the core schema (PLAN.md §4 "Data model").

Tables:
  games, game_tags            — platform-agnostic catalog
  signals_samples, _rollups   — time-series metrics per game
  news_items                  — news with pgvector embedding + cluster id
  recommendations, feedback   — the recommender's output + the feedback loop
  streamer_prefs              — personalization
  outbox                      — reliable, deduplicated notification delivery
  source_cursors              — per-source checkpoint state
  jobs                        — scheduler run state

Migrations are authored with alembic against these models; do not create tables
by hand in app code.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Keep in sync with EmbeddingsSettings.dim (bge-small-en-v1.5 => 384).
EMBEDDING_DIM = 384


class Base(DeclarativeBase):
    pass


class Platform(enum.StrEnum):
    STEAM = "steam"
    XBOX = "xbox"
    PSN = "psn"
    SWITCH = "switch"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ── Catalog ──────────────────────────────────────────────────────────────────


class Game(Base, TimestampMixin):
    __tablename__ = "games"
    __table_args__ = (UniqueConstraint("platform", "platform_app_id", name="uq_game_platform_app"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[Platform] = mapped_column(String(16), nullable=False, default=Platform.STEAM)
    platform_app_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    genres: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    release_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # When appdetails was last successfully fetched (crawler prioritization).
    details_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether we actively poll this game's player count (top-N + explicit tracks).
    tracked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Representative vector (name+genres) for similar-games nearest-neighbour
    # search (UI_PLAN.md §3.3, §8 UI-M3). Backfilled by the ``stats:refresh`` job;
    # nullable until that job embeds the game. HNSW cosine index in migration 0004.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))

    tags: Mapped[list[GameTag]] = relationship(back_populates="game", cascade="all, delete-orphan")


class GameTag(Base):
    __tablename__ = "game_tags"
    __table_args__ = (UniqueConstraint("game_id", "tag", name="uq_game_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    # Steam user-tag vote weight, when available.
    weight: Mapped[int | None] = mapped_column(Integer)

    game: Mapped[Game] = relationship(back_populates="tags")


# ── Signals (time series) ────────────────────────────────────────────────────


class SignalMetric(enum.StrEnum):
    PLAYERS = "players"  # concurrent players
    REVIEW_COUNT = "review_count"  # cumulative reviews
    NEWS_COUNT = "news_count"  # news items in window
    TWITCH_VIEWERS = "twitch_viewers"
    PRICE_CENTS = "price_cents"


class SignalSample(Base):
    """Append-only raw samples. One row per (game, metric, ts)."""

    __tablename__ = "signals_samples"
    __table_args__ = (
        UniqueConstraint("game_id", "metric", "ts", name="uq_sample"),
        Index("ix_sample_game_metric_ts", "game_id", "metric", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    metric: Mapped[SignalMetric] = mapped_column(String(32), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)


class SignalRollup(Base):
    """Pre-aggregated windows (e.g. 1d) for cheap slope/z-score queries."""

    __tablename__ = "signals_rollups"
    __table_args__ = (
        UniqueConstraint("game_id", "metric", "window", "bucket_start", name="uq_rollup"),
        Index("ix_rollup_game_metric_bucket", "game_id", "metric", "bucket_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    metric: Mapped[SignalMetric] = mapped_column(String(32), nullable=False)
    window: Mapped[str] = mapped_column(String(8), nullable=False)  # e.g. "1h", "1d"
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    avg: Mapped[float] = mapped_column(Float, nullable=False)
    min: Mapped[float] = mapped_column(Float, nullable=False)
    max: Mapped[float] = mapped_column(Float, nullable=False)
    samples: Mapped[int] = mapped_column(Integer, nullable=False)


# ── Precomputed list-row stats (UI_PLAN.md §5.4) ─────────────────────────────


class GameStats(Base):
    """Precomputed per-game catalog-row stats (UI_PLAN.md §5.4, UI-M2).

    Refreshed by the ``stats:refresh`` job every 15 min from ``signals_samples``
    so list pages join this small table instead of aggregating raw samples per
    row at request time. One row per game; ``game_id`` is both PK and FK.

    Sort-supporting indexes use ``NULLS LAST`` to match the keyset ordering of
    the catalog's numeric sorts.
    """

    __tablename__ = "game_stats"

    game_id: Mapped[int] = mapped_column(
        ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    current_players: Mapped[float | None] = mapped_column(Float)
    players_24h_delta: Mapped[float | None] = mapped_column(Float)
    # ≤21 floats, oldest→newest; [] when no data (never NULL).
    players_7d_spark: Mapped[list[float]] = mapped_column(JSONB, nullable=False, default=list)
    review_count: Mapped[float | None] = mapped_column(Float)
    twitch_viewers: Mapped[float | None] = mapped_column(Float)
    last_signal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# Sort indexes (DESC NULLS LAST) — declared post-class so the columns resolve.
Index(
    "ix_game_stats_players",
    GameStats.current_players.desc().nulls_last(),
)
Index(
    "ix_game_stats_delta",
    GameStats.players_24h_delta.desc().nulls_last(),
)
Index(
    "ix_game_stats_reviews",
    GameStats.review_count.desc().nulls_last(),
)


# ── News + enrichment ────────────────────────────────────────────────────────


class NewsItem(Base, TimestampMixin):
    __tablename__ = "news_items"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_news_natural_key"),
        Index("ix_news_game_published", "game_id", "published_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Populated by the enrichment module; nullable until embeddings run.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    cluster_id: Mapped[int | None] = mapped_column(Integer, index=True)


# ── Recommender output + feedback ────────────────────────────────────────────


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_rec_game_created", "game_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    # Per-component contributions for explainability ("why this game").
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedbackVerdict(enum.StrEnum):
    UP = "up"
    DOWN = "down"
    PLAYED = "played"


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    rec_id: Mapped[int] = mapped_column(ForeignKey("recommendations.id", ondelete="CASCADE"))
    verdict: Mapped[FeedbackVerdict] = mapped_column(String(16), nullable=False)


# ── Personalization ──────────────────────────────────────────────────────────


class StreamerPref(Base, TimestampMixin):
    """Single-row-ish profile keyed by a stable ``key`` (the streamer id)."""

    __tablename__ = "streamer_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default="default")
    liked_genres: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    blocked_genres: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Genres the streamer has *subscribed* to (M7): a hard "always cover this" set,
    # distinct from ``liked_genres`` (soft taste). Drives auto-tracking, the
    # ``genre_sub`` score component, and the digest quota. Server-default ``[]``.
    subscribed_genres: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    muted_game_ids: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Taste vector, updated from feedback; nullable until first computed.
    profile_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))


# ── Delivery outbox ──────────────────────────────────────────────────────────


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Outbox(Base, TimestampMixin):
    """Reliable, deduplicated notification delivery (PLAN.md §4.6)."""

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("channel", "dedup_key", name="uq_outbox_dedup"),
        Index("ix_outbox_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(256), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        String(16), nullable=False, default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Source + scheduler state ─────────────────────────────────────────────────


class SourceCursor(Base, TimestampMixin):
    """Per-source checkpoint state (last appid, ETag, news gid…)."""

    __tablename__ = "source_cursors"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobStatus(enum.StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class Job(Base):
    """Scheduler run log — one row per job execution."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_name_started", "name", "started_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[JobStatus] = mapped_column(String(16), nullable=False, default=JobStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
