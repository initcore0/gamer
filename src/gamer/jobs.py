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

from gamer.config import Settings, get_settings
from gamer.enrichment.jobs import enrich_news_once
from gamer.enrichment.llm import LLMSummarizer
from gamer.health import alert_stale_sources_once
from gamer.logging import get_logger
from gamer.notify import (
    Channel,
    Notification,
    aclose_transports,
    build_all_transports,
    dispatch_pending,
    enqueue,
)
from gamer.notify.digest import build_digest, build_scored_digest
from gamer.scheduler import Scheduler
from gamer.scoring.service import recommend
from gamer.signals.movers import top_movers
from gamer.signals.rollups import refresh_rollups
from gamer.signals.stats import embed_missing_game_embeddings, refresh_game_stats
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
    """Build → enqueue → dispatch the daily digest. Idempotent per day (outbox).

    Prefers the scored recommender (M3); falls back to the naive top-movers digest
    when the scorer has nothing yet (no components/candidates).
    """
    if not await _digest_enabled():
        log.info("digest_skipped", reason="disabled via /digest off")
        return
    settings = get_settings()

    recs = await recommend(limit=10)
    if recs:
        # Optional LLM blurb (M4). Fail-open: returns None when disabled/unreachable,
        # and the digest then renders exactly as it did before the LLM existed.
        summary = await LLMSummarizer().summarize_digest([r.name for r in recs])

        def _build(channel: Channel) -> Notification:
            return build_scored_digest(recs, channel=channel, summary=summary)

        source = "scorer"
    else:
        movers = await top_movers(limit=10)

        def _build(channel: Channel) -> Notification:
            return build_digest(movers, channel=channel)

        source = "movers"

    # Telegram group is always a target; Discord fans out only when configured.
    # Same content, different channel (and thus a distinct dedup_key) per build.
    channels = [Channel.TELEGRAM_GROUP]
    if settings.discord.enabled:
        channels.append(Channel.DISCORD)
    for channel in channels:
        await enqueue(_build(channel))

    transports = build_all_transports(settings)
    try:
        stats = await dispatch_pending(transports)
    finally:
        await aclose_transports(transports)
    log.info("digest_dispatched", source=source, sent=stats.sent, failed=stats.failed)


async def _digest_enabled(key: str = "default") -> bool:
    """The streamer's ``/digest on|off`` preference (default: on)."""
    from sqlalchemy import select

    from gamer.db import session_scope
    from gamer.db.models import StreamerPref

    async with session_scope() as session:
        enabled = (
            await session.execute(
                select(StreamerPref.digest_enabled).where(StreamerPref.key == key)
            )
        ).scalar_one_or_none()
    return True if enabled is None else bool(enabled)


async def run_health_check_once() -> None:
    """Alert the streamer about any newly-stale source (PLAN.md §6 M4)."""
    await alert_stale_sources_once()


async def run_stats_refresh_once() -> None:
    """Recompute catalog-row stats + backfill game embeddings (UI_PLAN.md §5.4, §3.3)."""
    await refresh_game_stats()
    # Similar-games backfill: embed a bounded batch of games missing a vector.
    await embed_missing_game_embeddings()


async def run_rollups_refresh_once() -> None:
    """Refresh the 1d signal rollups charts read beyond 7 days (UI_PLAN.md §5.5)."""
    await refresh_rollups()


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

    # News dedup/clustering (M4). Always-on: get_embedder() falls back to the
    # deterministic HashEmbedder when embeddings are disabled, so this needs no flag.
    scheduler.add_interval_job(enrich_news_once, seconds=6 * 3600, name="enrich:news")

    # Self-health: hourly stale-source check that pings the streamer once/day.
    scheduler.add_interval_job(run_health_check_once, seconds=3600, name="health")

    # Precomputed catalog-row stats so /games never aggregates signals per row.
    scheduler.add_interval_job(run_stats_refresh_once, seconds=15 * 60, name="stats:refresh")

    # Signal rollups (1d buckets) so game-detail charts read cheap history beyond 7d.
    scheduler.add_interval_job(run_rollups_refresh_once, seconds=3600, name="rollups:refresh")
