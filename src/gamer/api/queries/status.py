"""Dashboard/status query layer (UI_PLAN.md §4 — moved from ``api/app.py``).

Assembles the read-only ``/status`` payload: row counts, recent recommendations,
per-source last-run status, and which sources are STALE. Returns TypedDicts so
both the Jinja dashboard and the JSON twin share one shaped result. No writes,
no auth — safe for the public build log.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy import func, select

from gamer.db import session_scope
from gamer.db.models import Game, GameStats, NewsItem, Outbox, Recommendation, SignalSample
from gamer.health import find_stale_sources
from gamer.sources.runner import latest_source_status

_RECENT_REC_LIMIT = 10
_TOP_MOVERS_LIMIT = 5
_LATEST_REC_LIMIT = 5


class Counts(TypedDict):
    games: int
    news: int
    signals: int
    recommendations: int


class RecentRecommendation(TypedDict):
    name: str
    score: float
    created_at: str | None


class TopMover(TypedDict):
    game_id: int
    name: str
    delta: float
    spark: list[float]


class LatestRecommendation(TypedDict):
    game_id: int
    name: str
    score: float


class LastDigest(TypedDict):
    channel: str
    sent_at: str | None


class StatusPayload(TypedDict):
    generated_at: str
    sources: list[dict[str, Any]]
    stale_sources: list[str]
    counts: Counts
    recent_recommendations: list[RecentRecommendation]


async def counts() -> Counts:
    """Total row counts for the headline tiles."""
    async with session_scope() as session:
        games = await session.scalar(select(func.count()).select_from(Game))
        news = await session.scalar(select(func.count()).select_from(NewsItem))
        signals = await session.scalar(select(func.count()).select_from(SignalSample))
        recs = await session.scalar(select(func.count()).select_from(Recommendation))
    return Counts(
        games=int(games or 0),
        news=int(news or 0),
        signals=int(signals or 0),
        recommendations=int(recs or 0),
    )


async def recent_recommendations() -> list[RecentRecommendation]:
    """The most recent recommendations, newest first."""
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Recommendation.score, Game.name, Recommendation.created_at)
                .join(Game, Game.id == Recommendation.game_id)
                .order_by(Recommendation.created_at.desc())
                .limit(_RECENT_REC_LIMIT)
            )
        ).all()
    return [
        RecentRecommendation(
            name=name,
            score=round(float(score), 4),
            created_at=created_at.isoformat() if created_at else None,
        )
        for score, name, created_at in rows
    ]


async def top_movers() -> list[TopMover]:
    """Biggest 24h player gainers, read from the precomputed ``game_stats``.

    UI_PLAN.md §5.4: the dashboard strip reads the precomputed delta rather than
    aggregating ``signals_samples`` at request time. Only rows with a computed
    delta are shown, ordered ``players_24h_delta DESC``.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    GameStats.game_id,
                    Game.name,
                    GameStats.players_24h_delta,
                    GameStats.players_7d_spark,
                )
                .join(Game, Game.id == GameStats.game_id)
                .where(GameStats.players_24h_delta.is_not(None))
                .order_by(GameStats.players_24h_delta.desc())
                .limit(_TOP_MOVERS_LIMIT)
            )
        ).all()
    return [
        TopMover(
            game_id=int(game_id),
            name=name,
            delta=float(delta),
            spark=[float(x) for x in (spark or [])],
        )
        for game_id, name, delta, spark in rows
    ]


async def latest_recommendations() -> list[LatestRecommendation]:
    """Latest few recommendations with game_id, for the dashboard strip.

    UI-only (links to ``/games/{id}`` and ``/recommendations``) — deliberately
    separate from :func:`recent_recommendations`, whose shape is frozen by the
    backward-compatible ``/status`` payload.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Recommendation.game_id, Game.name, Recommendation.score)
                .join(Game, Game.id == Recommendation.game_id)
                .order_by(Recommendation.created_at.desc())
                .limit(_LATEST_REC_LIMIT)
            )
        ).all()
    return [
        LatestRecommendation(game_id=int(game_id), name=name, score=round(float(score), 4))
        for game_id, name, score in rows
    ]


async def last_digest() -> LastDigest | None:
    """The most recently created digest outbox row (channel + sent_at), or None.

    Matches ``dedup_key LIKE 'digest:%'`` (all digest channels). UI-only, so it
    stays out of the backward-compatible ``/status`` payload.
    """
    async with session_scope() as session:
        row = (
            await session.execute(
                select(Outbox.channel, Outbox.sent_at)
                .where(Outbox.dedup_key.like("digest:%"))
                .order_by(Outbox.created_at.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return None
    channel, sent_at = row
    return LastDigest(channel=str(channel), sent_at=sent_at.isoformat() if sent_at else None)


async def build_status(*, now: datetime | None = None) -> StatusPayload:
    """Assemble the full ``/status`` payload from read-only DB queries."""
    now = now or datetime.now(UTC)
    sources = await latest_source_status()
    row_counts = await counts()
    recent = await recent_recommendations()
    stale = await find_stale_sources(now)
    return StatusPayload(
        generated_at=now.isoformat(),
        sources=sources,
        stale_sources=stale,
        counts=row_counts,
        recent_recommendations=recent,
    )
