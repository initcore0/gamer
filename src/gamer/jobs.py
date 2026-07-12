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

from gamer.catalog.genre_tracking import track_subscribed_genres
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
from gamer.notify.digest import (
    build_digest,
    build_dm_digest,
    build_scored_digest,
    select_dm_digest_keys,
)
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
    # The GetAppList catalog endpoint is a single cheap call (not the rate-limited
    # appdetails one), so we can fill the catalog faster — ~5k/run fills Steam's
    # ~250k appids in a couple of days instead of ~10.
    "steam_api": 5000,  # catalog pages / player samples per run
    "steam_store": 100,  # appdetails is stingy — small batches, stays conservative
}


def _make_source_job(name: str) -> Callable[[], Awaitable[None]]:
    sink = DbEventSink()
    limit = _SOURCE_RUN_LIMITS.get(name)

    async def _job() -> None:
        source = REGISTRY[name]()
        await run_source(source, sink, limit=limit)

    return _job


async def run_digest_once() -> None:
    """Build → enqueue → dispatch the daily digests. Idempotent per day (outbox).

    Two fan-outs (multi-user):

    * the **group** broadcast — scored for the group's own profile
      (``str(group_chat_id)``), falling back to the legacy ``'default'`` profile
      when no group prefs row exists yet (so subscriptions keep applying right
      after the migration, before anyone talks to the bot). Also mirrored to
      Discord when configured.
    * a per-user **DM** digest for every prefs row with ``digest_enabled`` whose
      key is a DM chat (positive int, excluding the group). Each is scored for
      that user, deduped independently, and a failure for one user never aborts
      the others.

    Both prefer the scored recommender (M3), falling back to the naive top-movers
    digest when the scorer has nothing yet.
    """
    settings = get_settings()
    group_key = _group_digest_key(settings)

    # Enqueue every digest first, then dispatch the whole batch once.
    await _run_group_digest(settings, group_key)
    await _run_dm_digests(settings)

    transports = build_all_transports(settings)
    try:
        stats = await dispatch_pending(transports)
    finally:
        await aclose_transports(transports)
    log.info("digest_dispatched", sent=stats.sent, failed=stats.failed)


def _group_digest_key(settings: Settings) -> str:
    """The profile key the group digest scores against: the group chat id."""
    return str(settings.telegram.group_chat_id)


async def _run_group_digest(settings: Settings, group_key: str) -> None:
    """Enqueue the group (and Discord) digest scored for the group profile."""
    # Score for the group profile; if it has no prefs row yet, fall back to the
    # legacy 'default' profile so subscriptions keep applying post-migration.
    enabled = await _digest_enabled(group_key, fallback_key="default")
    if not enabled:
        log.info("digest_skipped", reason="group digest disabled via /digest off")
        return
    score_key = await _resolve_score_key(group_key, fallback_key="default")

    recs = await recommend(limit=10, key=score_key, subscribed_quota=3)
    if recs:
        summary = await LLMSummarizer().summarize_digest([r.name for r in recs])

        def _build(channel: Channel) -> Notification:
            return build_scored_digest(
                recs,
                channel=channel,
                summary=summary,
                public_base_url=settings.ui.public_base_url,
            )
    else:
        movers = await top_movers(limit=10)

        def _build(channel: Channel) -> Notification:
            return build_digest(movers, channel=channel)

    # Telegram group is always a target; Discord fans out only when configured.
    channels = [Channel.TELEGRAM_GROUP]
    if settings.discord.enabled:
        channels.append(Channel.DISCORD)
    for channel in channels:
        await enqueue(_build(channel))


async def _run_dm_digests(settings: Settings) -> None:
    """Enqueue a per-user DM digest for each qualifying prefs profile (multi-user).

    One failure never aborts the rest: each user's scoring is guarded so a single
    bad profile is logged and skipped. Empty recs => skip silently (no spam).
    """
    rows = await _all_digest_prefs()
    chat_ids = select_dm_digest_keys(rows, group_chat_id=settings.telegram.group_chat_id)
    for chat_id in chat_ids:
        try:
            recs = await recommend(limit=10, key=str(chat_id), subscribed_quota=3)
            if not recs:
                continue
            summary = await LLMSummarizer().summarize_digest([r.name for r in recs])
            await enqueue(
                build_dm_digest(
                    recs,
                    chat_id=chat_id,
                    summary=summary,
                    public_base_url=settings.ui.public_base_url,
                )
            )
        except Exception:
            # A per-user failure must not take down every other user's digest.
            log.exception("dm_digest_failed", chat_id=chat_id)


async def _all_digest_prefs() -> list[tuple[str, bool]]:
    """Every prefs row as ``(key, digest_enabled)`` — the DM fan-out selection set."""
    from sqlalchemy import select

    from gamer.db import session_scope
    from gamer.db.models import StreamerPref

    async with session_scope() as session:
        rows = (await session.execute(select(StreamerPref.key, StreamerPref.digest_enabled))).all()
    return [(key, bool(enabled)) for key, enabled in rows]


async def _digest_enabled(key: str, *, fallback_key: str | None = None) -> bool:
    """Whether profile ``key`` has the digest enabled (default: on for a missing row).

    When ``key`` has no prefs row and ``fallback_key`` is given, the fallback
    profile's preference is used instead (so the group digest honors the legacy
    ``'default'`` toggle until a group profile exists).
    """
    from sqlalchemy import select

    from gamer.db import session_scope
    from gamer.db.models import StreamerPref

    async with session_scope() as session:
        enabled = (
            await session.execute(
                select(StreamerPref.digest_enabled).where(StreamerPref.key == key)
            )
        ).scalar_one_or_none()
        if enabled is None and fallback_key is not None:
            enabled = (
                await session.execute(
                    select(StreamerPref.digest_enabled).where(StreamerPref.key == fallback_key)
                )
            ).scalar_one_or_none()
    return True if enabled is None else bool(enabled)


async def _resolve_score_key(key: str, *, fallback_key: str) -> str:
    """Return ``key`` if it has a prefs row, else ``fallback_key`` (group→default)."""
    from sqlalchemy import select

    from gamer.db import session_scope
    from gamer.db.models import StreamerPref

    async with session_scope() as session:
        exists = (
            await session.execute(select(StreamerPref.id).where(StreamerPref.key == key))
        ).scalar_one_or_none()
    return key if exists is not None else fallback_key


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


async def run_genre_track_once() -> None:
    """Auto-track the top games of each subscribed genre (GENRE_SUBS_PLAN.md, M7).

    Degrade-don't-crash: exceptions are logged and swallowed so a bad run never
    takes down the scheduler.
    """
    try:
        newly = await track_subscribed_genres()
        log.info("genre_track_done", newly_tracked=newly)
    except Exception:  # a scheduled job must never crash the loop
        log.exception("genre_track_failed")


def register_jobs(scheduler: Scheduler, settings: Settings) -> None:
    """Register source-poll jobs and the daily digest with the scheduler."""
    for name, factory in REGISTRY.items():
        interval = factory().default_interval_seconds
        scheduler.add_interval_job(_make_source_job(name), seconds=interval, name=f"poll:{name}")

    # Daily digest at a fixed UTC hour. A cron trigger (not a 24h interval) so a
    # restart never delays the digest by a day. Guarded by the group chat being
    # configured.
    if settings.telegram.group_chat_id:
        scheduler.add_daily_job(
            run_digest_once, hour=settings.telegram.digest_hour_utc, name="digest"
        )
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

    # Genre subscriptions: auto-track the top games of each subscribed genre so
    # niche-genre coverage stays fresh as the appdetails crawler fills genres.
    scheduler.add_interval_job(run_genre_track_once, seconds=3600, name="genre:track")
