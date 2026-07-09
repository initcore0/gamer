"""RSS / Atom news source adapter (PLAN.md §3 — broader news).

Fetches a configurable list of gaming-news feeds (PC Gamer, RPS, Eurogamer…)
and emits one :class:`EventKind.NEWS` event per entry. These are broad, editorial
stories not tied to a specific Steam app, so ``platform_app_id`` is ``None`` — the
sink stores them with a null ``game_id``.

Idempotency: each entry's natural key is its feed-provided id/guid, or (when the
feed omits one) a stable hash of ``link + title``. The sink upserts news on
``(source, external_id)``, so a stable natural key makes re-ingestion a no-op.

Politeness / dedup cursor: per feed we remember the newest entry id seen and its
timestamp. On the next run we stop walking a feed as soon as we reach an already
-seen entry, so a quiet feed emits nothing and we never re-download the same
stories into the pipeline. Feeds are typically newest-first; we also short-circuit
on entries at-or-before the last-seen timestamp as a fallback for feeds whose ids
churn.

Resilience: a bad, unreachable, or malformed feed never crashes the run — it is
logged (URLs passed through :func:`redact_secrets`) and skipped, per the
:class:`Source` contract.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
import httpx

from gamer.config import get_settings
from gamer.logging import get_logger, redact_secrets
from gamer.sources.base import EventKind, FetchContext, RawEvent
from gamer.sources.http import PoliteClient, RetryableStatus

log = get_logger("sources.rss")

# Upstream failures that are expected and must degrade (log + skip the feed)
# rather than crash the run: httpx transport/status errors plus PoliteClient's
# retry-exhausted 429/5xx signal.
_UPSTREAM_ERRORS = (httpx.HTTPError, RetryableStatus)


def _entry_natural_key(entry: Any) -> str:
    """Stable per-entry id: the feed's id/guid, else a hash of link+title."""
    raw_id = entry.get("id") or entry.get("guid")
    if raw_id:
        return str(raw_id)
    link = entry.get("link") or ""
    title = entry.get("title") or ""
    digest = hashlib.sha256(f"{link}\n{title}".encode()).hexdigest()
    return f"sha256:{digest}"


def _struct_to_dt(value: Any) -> datetime | None:
    """Convert a feedparser ``struct_time`` (always UTC) to an aware datetime."""
    if not isinstance(value, struct_time):
        return None
    return datetime(*value[:6], tzinfo=UTC)


def _entry_published(entry: Any, fallback: datetime) -> datetime:
    """Aware-UTC publish time: published, else updated, else ``fallback``."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = _struct_to_dt(entry.get(key))
        if parsed is not None:
            return parsed
    return fallback


class RssSource:
    """RSS/Atom broader-news adapter. Implements the :class:`Source` protocol."""

    name = "rss"
    default_interval_seconds = 1800  # ~30 min

    def __init__(
        self,
        *,
        feeds: Sequence[str] | None = None,
        rate: int = 10,
        per: float = 60.0,
        max_attempts: int = 4,
    ) -> None:
        # Injectable so unit tests need no config monkeypatching; defaults to the
        # configured feed list (read lazily in ``fetch`` when not injected).
        self._feeds = list(feeds) if feeds is not None else None
        self._rate = rate
        self._per = per
        self._max_attempts = max_attempts

    def _client(self) -> PoliteClient:
        return PoliteClient(rate=self._rate, per=self._per, max_attempts=self._max_attempts)

    def _resolve_feeds(self) -> list[str]:
        if self._feeds is not None:
            return self._feeds
        return list(get_settings().rss.feeds)

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        """Yield NEWS events across all configured feeds.

        No-op when RSS is disabled. Honours ``ctx.limit`` (soft cap across all
        feeds) and mutates ``ctx.cursor`` in place: ``cursor["feeds"][url]`` holds
        ``{"last_id", "last_ts"}`` for per-feed dedup. Never raises on a bad feed.
        """
        settings = get_settings()
        if not settings.rss.enabled:
            return

        feeds = self._resolve_feeds()
        if not feeds:
            return

        feed_cursors: dict[str, Any] = ctx.cursor.setdefault("feeds", {})
        emitted = 0

        async with self._client() as client:
            for url in feeds:
                async for event in self._fetch_feed(client, url, feed_cursors):
                    emitted += 1
                    yield event
                    if ctx.limit is not None and emitted >= ctx.limit:
                        return

    async def _fetch_feed(
        self, client: PoliteClient, url: str, feed_cursors: dict[str, Any]
    ) -> AsyncIterator[RawEvent]:
        """Emit NEWS events for one feed, stopping at already-seen entries.

        Advances ``feed_cursors[url]`` to the newest entry seen so the next run
        dedups. A single feed's failure is logged and swallowed.
        """
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
        except _UPSTREAM_ERRORS as exc:
            log.warning(
                "rss_fetch_failed",
                url=redact_secrets(url),
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return

        try:
            parsed = feedparser.parse(content)
        except Exception as exc:  # feedparser is defensive, but never crash the run.
            log.warning(
                "rss_parse_failed",
                url=redact_secrets(url),
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return

        entries = parsed.get("entries") or []
        if not entries:
            # bozo feeds with no usable entries (malformed XML) land here.
            if parsed.get("bozo"):
                bozo_exc = parsed.get("bozo_exception")
                log.warning(
                    "rss_malformed",
                    url=redact_secrets(url),
                    error=redact_secrets(f"{type(bozo_exc).__name__}: {bozo_exc}")
                    if bozo_exc
                    else "bozo",
                )
            return

        cursor = feed_cursors.get(url) or {}
        seen_id = cursor.get("last_id")
        seen_ts = _parse_iso(cursor.get("last_ts"))

        now = datetime.now(UTC)
        newest_id: str | None = None
        newest_ts: datetime | None = None

        for entry in entries:
            natural_key = _entry_natural_key(entry)
            published = _entry_published(entry, now)

            # Stop at content we've already emitted (feeds are newest-first): an id
            # match, or an entry at/older than the newest timestamp we saw last run.
            if seen_id is not None and natural_key == seen_id:
                break
            if seen_ts is not None and published <= seen_ts:
                break

            # Track the newest (first) entry for the next run's checkpoint.
            if newest_id is None:
                newest_id = natural_key
                newest_ts = published

            title = entry.get("title") or ""
            body = entry.get("summary") or entry.get("description") or ""
            link = entry.get("link") or ""

            yield RawEvent(
                source=self.name,
                kind=EventKind.NEWS,
                natural_key=natural_key,
                payload={
                    "title": title,
                    "url": link,
                    "body": body,
                    "published_at": published.isoformat(),
                },
                occurred_at=published,
                platform_app_id=None,  # broader news, not tied to a specific game.
                fetched_at=now,
                meta={"feed": url},
            )

        # Advance the checkpoint to the newest entry we saw this run (if any).
        if newest_id is not None:
            feed_cursors[url] = {
                "last_id": newest_id,
                "last_ts": newest_ts.isoformat() if newest_ts is not None else None,
            }


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
