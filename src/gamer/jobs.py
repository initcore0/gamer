"""Scheduled jobs — the wiring that turns modules into a running system (M1/M2).

Two kinds of job:
  * source polls — one per registered source; runs the adapter through the
    ingestion runner with the DB sink on the source's own interval.
  * digest — builds the "top movers" digest, enqueues it to the outbox, and
    dispatches pending deliveries to Telegram.

Kept separate from ``app.py`` so the wiring is testable and the app module stays
a thin lifecycle shell.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from gamer.config import Settings
from gamer.logging import get_logger
from gamer.notify import Channel, dispatch_pending, enqueue
from gamer.notify.digest import build_digest
from gamer.notify.telegram import build_telegram_transports
from gamer.scheduler import Scheduler
from gamer.signals.movers import top_movers
from gamer.sources import REGISTRY
from gamer.sources.runner import run_source
from gamer.sources.sink import DbEventSink

log = get_logger("jobs")

# Per-run soft caps so a single poll stays polite and bounded.
_SOURCE_RUN_LIMITS = {
    "steam_api": 1000,  # catalog pages / player samples per run
    "steam_store": 100,  # appdetails is stingy — small batches
}


def _make_source_job(name: str) -> Callable[[], Awaitable[None]]:
    sink = DbEventSink()
    limit = _SOURCE_RUN_LIMITS.get(name)

    async def _job() -> None:
        source = REGISTRY[name]()
        await run_source(source, sink, limit=limit)

    return _job


async def run_digest_once() -> None:
    """Build → enqueue → dispatch the daily digest. Idempotent per day (outbox)."""
    movers = await top_movers(limit=10)
    notification = build_digest(movers, channel=Channel.TELEGRAM_GROUP)
    await enqueue(notification)
    transports = build_telegram_transports()
    stats = await dispatch_pending(transports)
    log.info("digest_dispatched", movers=len(movers), sent=stats.sent, failed=stats.failed)


def register_jobs(scheduler: Scheduler, settings: Settings) -> None:
    """Register source-poll jobs and the daily digest with the scheduler."""
    for name, factory in REGISTRY.items():
        interval = factory().default_interval_seconds
        scheduler.add_interval_job(_make_source_job(name), seconds=interval, name=f"poll:{name}")

    # Daily digest. Guarded by the group chat being configured.
    if settings.telegram.group_chat_id:
        scheduler.add_interval_job(run_digest_once, seconds=24 * 3600, name="digest")
    else:
        log.info("digest_disabled", reason="no group_chat_id configured")
