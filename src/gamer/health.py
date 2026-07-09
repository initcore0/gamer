"""Self-reported health — stale-source detection + alerting (PLAN.md §6 M4).

The system watches its own ingestion: a source that hasn't *succeeded* within
``settings.health.stale_after_hours`` is "stale" and pings the streamer once per
day via a Telegram DM (through the outbox, so the alert is deduplicated and
retried like any other notification).

The detection itself (:func:`find_stale_sources` /
:func:`stale_sources_from_rows`) is pure-ish: ``now`` is injected so the
threshold logic can be unit-tested without a clock or a database. Only
:func:`find_stale_sources` and :func:`alert_stale_sources_once` touch the DB.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from gamer.config import Settings, get_settings
from gamer.db import session_scope
from gamer.db.models import SourceCursor
from gamer.logging import get_logger
from gamer.notify import (
    Channel,
    Notification,
    build_telegram_transports,
    dispatch_pending,
    enqueue,
)

log = get_logger("health")


def stale_dedup_key(source: str, now: datetime) -> str:
    """Dedup key for one stale alert: at most one per source per calendar day."""
    return f"stale:{source}:{now.date().isoformat()}"


def stale_sources_from_rows(
    rows: Iterable[SourceCursor],
    *,
    now: datetime,
    stale_after_hours: int,
) -> list[str]:
    """Pure stale-detection: which sources' ``last_success_at`` is too old.

    A source is stale when it has run at least once (``last_run_at`` set) but its
    last *success* is older than the threshold — or it has never succeeded at all.
    Sources that have never run are ignored (nothing to alert on yet).
    """
    threshold = now - timedelta(hours=stale_after_hours)
    stale: list[str] = []
    for row in rows:
        if row.last_run_at is None:
            continue  # never ran — no signal yet
        if row.last_success_at is None or row.last_success_at < threshold:
            stale.append(row.source)
    return sorted(stale)


async def find_stale_sources(
    now: datetime | None = None, *, settings: Settings | None = None
) -> list[str]:
    """Sources whose last success is older than ``stale_after_hours``.

    ``now`` is injected for tests; defaults to the current UTC time.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    async with session_scope() as session:
        rows = (await session.execute(select(SourceCursor))).scalars().all()
    return stale_sources_from_rows(
        rows, now=now, stale_after_hours=settings.health.stale_after_hours
    )


def _stale_notification(source: str, now: datetime, stale_after_hours: int) -> Notification:
    return Notification(
        channel=Channel.TELEGRAM_DM,
        text=(
            f"⚠️ Source <b>{source}</b> has been silent for over "
            f"{stale_after_hours}h — no successful run. Check the ingestion logs."
        ),
        dedup_key=stale_dedup_key(source, now),
        meta={"parse_mode": "HTML", "disable_web_page_preview": True},
    )


async def alert_stale_sources_once(
    now: datetime | None = None, *, settings: Settings | None = None
) -> list[str]:
    """Find stale sources and DM the streamer once per source per day.

    Each alert is enqueued with a ``stale:<source>:<date>`` dedup key so the
    outbox drops repeat enqueues within the same day — a source that stays stale
    is only pinged once. No-op when no Telegram token is configured (the DM
    transport has nowhere to send). Returns the stale sources found.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)

    stale = await find_stale_sources(now, settings=settings)
    if not stale:
        return []

    if not settings.telegram.bot_token.get_secret_value():
        log.info("health.alert_skipped", reason="no bot_token configured", stale=stale)
        return stale

    for source in stale:
        await enqueue(_stale_notification(source, now, settings.health.stale_after_hours))

    transports = build_telegram_transports(settings)
    try:
        stats = await dispatch_pending(transports)
    finally:
        # Both transports share one aiogram Bot; close its HTTP session so each
        # run doesn't leak an aiohttp connector.
        await next(iter(transports.values())).aclose()

    log.info("health.alerted", stale=stale, sent=stats.sent, skipped=stats.skipped)
    return stale
