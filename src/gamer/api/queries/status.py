"""Dashboard/status query layer (UI_PLAN.md §4 — moved from ``api/app.py``).

Assembles the read-only ``/status`` payload: row counts, recent recommendations,
per-source last-run status, and which sources are STALE. Returns TypedDicts so
both the Jinja dashboard and the JSON twin share one shaped result. No writes,
no auth — safe for the public build log.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class DashboardTopMover(TypedDict):
    game_id: int
    name: str
    latest: float | None
    delta: float
    pct: float | None


class DashboardRecommendation(TypedDict):
    id: int
    game_id: int
    game_name: str
    score: float
    user_key: str
    created_at: str | None


class DashboardPayload(TypedDict):
    top_movers: list[DashboardTopMover]
    latest_recommendations: list[DashboardRecommendation]
    last_digest: LastDigest | None
    next_digest_at: str | None


async def dashboard_top_movers() -> list[DashboardTopMover]:
    """Top 24h player gainers in the contract's JSON shape (API_CONTRACT.md).

    Same ordering/window as :func:`top_movers` (precomputed ``game_stats`` delta,
    never ``signals_samples`` at request time — UI_PLAN.md §5.4), but shaped for
    the JSON dashboard: ``latest`` current players and the percentage growth
    ``pct`` (``delta`` over the pre-delta baseline) instead of the sparkline the
    HTML strip uses.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    GameStats.game_id,
                    Game.name,
                    GameStats.current_players,
                    GameStats.players_24h_delta,
                )
                .join(Game, Game.id == GameStats.game_id)
                .where(GameStats.players_24h_delta.is_not(None))
                .order_by(GameStats.players_24h_delta.desc())
                .limit(_TOP_MOVERS_LIMIT)
            )
        ).all()
    movers: list[DashboardTopMover] = []
    for game_id, name, latest, delta in rows:
        latest_f = None if latest is None else float(latest)
        delta_f = float(delta)
        # pct = growth over the pre-delta baseline (latest - delta); None when the
        # baseline is unknown or zero (avoid div-by-zero and misleading numbers).
        pct: float | None = None
        if latest_f is not None:
            baseline = latest_f - delta_f
            if baseline != 0:
                pct = round(delta_f / baseline * 100, 1)
        movers.append(
            DashboardTopMover(
                game_id=int(game_id),
                name=name,
                latest=latest_f,
                delta=delta_f,
                pct=pct,
            )
        )
    return movers


async def dashboard_latest_recommendations() -> list[DashboardRecommendation]:
    """Latest recommendations in the contract's JSON shape (API_CONTRACT.md).

    Like :func:`latest_recommendations` but carries the rec ``id``, owning
    ``user_key`` (multi-user ``pref_key``), and ``created_at`` the JSON dashboard
    needs. Newest first.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    Recommendation.id,
                    Recommendation.game_id,
                    Game.name,
                    Recommendation.score,
                    Recommendation.pref_key,
                    Recommendation.created_at,
                )
                .join(Game, Game.id == Recommendation.game_id)
                .order_by(Recommendation.created_at.desc(), Recommendation.id.desc())
                .limit(_LATEST_REC_LIMIT)
            )
        ).all()
    return [
        DashboardRecommendation(
            id=int(rec_id),
            game_id=int(game_id),
            game_name=name,
            score=round(float(score), 4),
            user_key=str(pref_key),
            created_at=created_at.isoformat() if created_at else None,
        )
        for rec_id, game_id, name, score, pref_key, created_at in rows
    ]


def next_digest_at(
    digest_hour_utc: int,
    *,
    group_chat_id: int,
    now: datetime,
) -> datetime | None:
    """Next UTC instant the daily digest fires, or ``None`` if no group chat.

    Pure helper (unit-tested, DB-free). The digest is a daily cron at
    ``digest_hour_utc`` UTC; the next fire is today at that hour if it is still
    ahead of ``now``, otherwise tomorrow. Returns ``None`` when no group chat is
    configured (``group_chat_id == 0``), since the digest has nowhere to go.

    ``now`` is coerced to UTC; a naive ``now`` is assumed to already be UTC.
    """
    if group_chat_id == 0:
        return None
    now_utc = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    fire = now_utc.replace(hour=digest_hour_utc, minute=0, second=0, microsecond=0)
    if fire <= now_utc:
        fire += timedelta(days=1)
    return fire


async def build_dashboard(*, now: datetime | None = None) -> DashboardPayload:
    """Assemble the ``/api/v1/dashboard`` payload (API_CONTRACT.md).

    Everything the dashboard page renders beyond ``/status``: the top-movers
    strip, the latest recommendations, the last digest, and the next digest fire
    time. Reuses the same read-only helpers as the HTML dashboard. ``now`` is
    injectable so the ``next_digest_at`` computation is deterministic in tests.
    """
    from gamer.config import get_settings

    now = now or datetime.now(UTC)
    settings = get_settings()
    movers = await dashboard_top_movers()
    latest = await dashboard_latest_recommendations()
    digest = await last_digest()
    next_at = next_digest_at(
        settings.telegram.digest_hour_utc,
        group_chat_id=settings.telegram.group_chat_id,
        now=now,
    )
    return DashboardPayload(
        top_movers=movers,
        latest_recommendations=latest,
        last_digest=digest,
        next_digest_at=next_at.isoformat() if next_at is not None else None,
    )
