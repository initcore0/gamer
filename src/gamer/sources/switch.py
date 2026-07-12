"""Switch eShop release source adapter (PLAN.md §6 M5 — extensibility stub).

This source exists to *prove the platform abstraction*: it fetches a free public
feed of Switch eShop releases and emits catalog events tagged with
``platform="switch"`` (the :class:`~gamer.db.models.Platform.SWITCH` enum value).
Because the M5-prep :class:`~gamer.sources.sink.DbEventSink` resolves each event's
platform (falling back to its Steam default only when ``event.platform is None``),
the SAME shared sink persists these games under the Switch platform — no new sink,
no jobs.py change. That seam is the whole point of this milestone.

Feed format is deliberately tolerant. We accept either:
  * a JSON payload (a top-level list, or an object with a ``games``/``results``/
    ``items``/``entries`` list), or
  * an RSS/Atom feed parsed by feedparser (already a dependency).
Each entry becomes an :class:`EventKind.RELEASE` event carrying ``name`` and,
when present, ``release_date`` / ``genres``.

Idempotency: the natural key is the feed's stable per-title id (``nsuid``/``id``/
``guid``), else a stable hash of the title slug. ``platform_app_id`` is that same
id coerced to a stable integer (the nsuid if numeric, otherwise a stable hash of
the slug), so the sink keys the Game on ``(switch, platform_app_id)``.

Cursor / dedup: we remember the newest release id and date seen. On the next run
we skip entries we've already emitted (id match or release date at/older than the
checkpoint), so a quiet feed emits nothing.

Resilience: an unreachable, 5xx, or malformed feed never crashes the run — it is
logged (URLs via :func:`redact_secrets`) and the run stops cleanly, per the
:class:`Source` contract.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import struct_time
from typing import Any

import feedparser
import httpx

from gamer.config import get_settings
from gamer.logging import get_logger, redact_secrets
from gamer.sources.base import EventKind, FetchContext, RawEvent
from gamer.sources.http import PoliteClient, RetryableStatus

log = get_logger("sources.switch")

# Upstream failures that must degrade (log + stop) rather than crash the run.
_UPSTREAM_ERRORS = (httpx.HTTPError, RetryableStatus)

# Keys under which a JSON feed may nest its list of releases.
_LIST_KEYS = ("games", "results", "items", "entries", "data")

# 63-bit positive int space for hashed ids: fits Game.platform_app_id (BigInteger)
# and stays a stable, deterministic surrogate when the feed has no numeric id.
_HASH_MOD = 1 << 63


def _slugify(title: str) -> str:
    """Stable lowercase slug for hashing id-less titles."""
    return "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")


def _stable_int_id(raw_id: str | None, title: str) -> int:
    """A stable positive integer id for a title.

    Prefers the feed's numeric id (e.g. an nsuid). Falls back to a deterministic
    hash of the title slug so id-less feeds still key their games consistently.
    """
    if raw_id is not None:
        stripped = raw_id.strip()
        if stripped.isdigit():
            return int(stripped)
    digest = hashlib.sha256(_slugify(title).encode()).hexdigest()
    return int(digest, 16) % _HASH_MOD


def _natural_key(raw_id: str | None, title: str) -> str:
    """The feed's stable per-title id, else a slug-hash fallback."""
    if raw_id is not None and raw_id.strip():
        return raw_id.strip()
    return f"slug:{_slugify(title)}"


def _parse_release_date(value: Any) -> str | None:
    """Normalize a release date to an ISO string, or ``None`` if unparseable.

    Accepts ISO-8601 strings (with a bare ``YYYY-MM-DD`` promoted to UTC midnight)
    and feedparser ``struct_time`` values (always UTC).
    """
    if isinstance(value, struct_time):
        return datetime(*value[:6], tzinfo=UTC).isoformat()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    return None


def _extract_genres(value: Any) -> list[str] | None:
    """Coerce a feed's genre field (list or comma string) to a list of strings."""
    if isinstance(value, list):
        genres = [str(g).strip() for g in value if str(g).strip()]
        return genres or None
    if isinstance(value, str):
        genres = [g.strip() for g in value.split(",") if g.strip()]
        return genres or None
    return None


def _normalize_entries(payload: Any) -> list[dict[str, Any]]:
    """Pull a list of title dicts from a tolerant JSON payload shape."""
    if isinstance(payload, list):
        return [e for e in payload if isinstance(e, dict)]
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [e for e in value if isinstance(e, dict)]
    return []


def _entry_fields(entry: dict[str, Any]) -> tuple[str | None, str, Any, Any]:
    """Extract (raw_id, name, release_date, genres) from one JSON entry."""
    raw_id = entry.get("nsuid") or entry.get("id") or entry.get("guid")
    raw_id_str = str(raw_id) if raw_id is not None else None
    name = str(entry.get("name") or entry.get("title") or "").strip()
    release_date = entry.get("release_date") or entry.get("releaseDate") or entry.get("released")
    genres = entry.get("genres") or entry.get("genre") or entry.get("categories")
    return raw_id_str, name, release_date, genres


class SwitchSource:
    """Switch eShop release adapter. Implements the :class:`Source` protocol.

    Emits ``platform="switch"`` catalog events so the shared Steam-default sink
    persists them under the Switch platform — proving the platform seam.
    """

    name = "switch"
    default_interval_seconds = 6 * 3600  # eShop releases move slowly; ~6h is plenty.

    def __init__(
        self,
        *,
        feed_url: str | None = None,
        rate: int = 10,
        per: float = 60.0,
        max_attempts: int = 4,
    ) -> None:
        # Injectable so unit tests need no config monkeypatching; ``None`` means
        # "read get_settings().switch.feed_url lazily in fetch".
        self._feed_url = feed_url
        self._rate = rate
        self._per = per
        self._max_attempts = max_attempts

    def _client(self) -> PoliteClient:
        return PoliteClient(rate=self._rate, per=self._per, max_attempts=self._max_attempts)

    def _resolve_feed_url(self) -> str:
        if self._feed_url is not None:
            return self._feed_url
        return get_settings().switch.feed_url

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        """Yield RELEASE events for new Switch eShop titles.

        No-op when Switch is disabled or no feed_url is configured. Honours
        ``ctx.limit`` and mutates ``ctx.cursor`` in place (``last_id`` / ``last_date``
        for dedup). Never raises on expected upstream failures.
        """
        settings = get_settings()
        if not settings.switch.enabled:
            return

        feed_url = self._resolve_feed_url()
        if not feed_url:
            return

        content = await self._fetch_content(feed_url)
        if content is None:
            return

        entries = self._parse(content, feed_url)
        if not entries:
            return

        seen_id = ctx.cursor.get("last_id")
        seen_date = _parse_iso(ctx.cursor.get("last_date"))

        now = datetime.now(UTC)
        newest_id: str | None = None
        newest_date: datetime | None = None
        emitted = 0

        for raw_id, name, release_date, genres in entries:
            if not name:
                continue
            natural_key = _natural_key(raw_id, name)
            iso_date = _parse_release_date(release_date)
            entry_date = _parse_iso(iso_date)

            # Skip content we've already emitted. Match the exact resume id, and
            # use strict ``<`` on the date: a same-date entry that is NOT the resume
            # id may be a NEW release the eShop feed added later that day — ``<=``
            # dropped it forever. The sink dedups by natural key, so re-seeing an
            # already-emitted same-date entry is a harmless upsert, not a duplicate.
            if seen_id is not None and natural_key == seen_id:
                continue
            if seen_date is not None and entry_date is not None and entry_date < seen_date:
                continue

            # Track the newest entry for the next run's checkpoint. "Newest" is by
            # release date when available, else the first entry seen.
            if newest_id is None or (
                entry_date is not None and (newest_date is None or entry_date > newest_date)
            ):
                newest_id = natural_key
                newest_date = entry_date

            payload: dict[str, Any] = {"name": name}
            if iso_date is not None:
                payload["release_date"] = iso_date
            parsed_genres = _extract_genres(genres)
            if parsed_genres is not None:
                payload["genres"] = parsed_genres

            yield RawEvent(
                source=self.name,
                kind=EventKind.RELEASE,
                natural_key=natural_key,
                payload=payload,
                occurred_at=entry_date or now,
                platform_app_id=_stable_int_id(raw_id, name),
                # CRUCIAL: this is what proves the platform abstraction — these
                # games land under Platform.SWITCH, not the sink's Steam default.
                platform="switch",
                fetched_at=now,
                meta={"feed": feed_url},
            )
            emitted += 1
            if ctx.limit is not None and emitted >= ctx.limit:
                break

        if newest_id is not None:
            ctx.cursor["last_id"] = newest_id
            ctx.cursor["last_date"] = newest_date.isoformat() if newest_date is not None else None

    async def _fetch_content(self, feed_url: str) -> bytes | None:
        """GET the feed, degrading (log + None) on expected upstream failures."""
        try:
            async with self._client() as client:
                resp = await client.get(feed_url)
                resp.raise_for_status()
                return resp.content
        except _UPSTREAM_ERRORS as exc:
            log.warning(
                "switch_fetch_failed",
                url=redact_secrets(feed_url),
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return None

    def _parse(self, content: bytes, feed_url: str) -> list[tuple[str | None, str, Any, Any]]:
        """Parse feed content (JSON first, RSS/Atom fallback) into entry tuples.

        Never raises: a malformed feed is logged and yields an empty list.
        """
        # Try JSON first — the common shape for a free eShop releases API.
        try:
            payload = json.loads(content)
        except (ValueError, UnicodeDecodeError):
            payload = None

        if payload is not None:
            entries = _normalize_entries(payload)
            if entries:
                return [_entry_fields(e) for e in entries]
            log.warning("switch_empty_json", url=redact_secrets(feed_url))
            return []

        # Fall back to RSS/Atom via feedparser.
        try:
            parsed = feedparser.parse(content)
        except Exception as exc:  # feedparser is defensive; never crash the run.
            log.warning(
                "switch_parse_failed",
                url=redact_secrets(feed_url),
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return []

        feed_entries = parsed.get("entries") or []
        if not feed_entries:
            if parsed.get("bozo"):
                bozo_exc = parsed.get("bozo_exception")
                log.warning(
                    "switch_malformed",
                    url=redact_secrets(feed_url),
                    error=redact_secrets(f"{type(bozo_exc).__name__}: {bozo_exc}")
                    if bozo_exc
                    else "bozo",
                )
            return []

        results: list[tuple[str | None, str, Any, Any]] = []
        for entry in feed_entries:
            raw_id = entry.get("id") or entry.get("guid")
            raw_id_str = str(raw_id) if raw_id else None
            name = str(entry.get("title") or "").strip()
            release_date: Any = entry.get("published_parsed") or entry.get("updated_parsed")
            genres = [t.get("term") for t in entry.get("tags", []) if t.get("term")] or None
            results.append((raw_id_str, name, release_date, genres))
        return results


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
