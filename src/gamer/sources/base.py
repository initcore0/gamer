"""The Source contract — the interface every upstream adapter implements.

This is the M1 delegation boundary (PLAN.md §8): one agent per source adapter,
all coding against this protocol. A source is idempotent (natural keys), polite
(rate-limited, honours caching), and degrades rather than crashes when its
upstream misbehaves.
"""

from __future__ import annotations

import enum
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


class EventKind(enum.StrEnum):
    """The normalized event types a source can emit."""

    GAME = "game"  # catalog entry (appid discovered) or details refresh
    NEWS = "news"  # a news / patch-note item
    PLAYER_COUNT = "player_count"  # a player-count sample
    PRICE = "price"  # a price change
    REVIEW = "review"  # review-count / velocity sample
    RELEASE = "release"  # a release or major update
    TWITCH = "twitch"  # streaming-meta sample (viewers)


@dataclass(slots=True)
class RawEvent:
    """A normalized event emitted by a source, before catalog/signals persistence.

    Idempotency contract: ``(source, kind, natural_key)`` uniquely identifies an
    event. Re-fetching the same upstream item MUST produce the same triple so the
    persistence layer can upsert safely.

    ``occurred_at`` is when the event happened upstream (news publish time, sample
    time). ``payload`` carries kind-specific fields — the persistence layer knows
    how to read each kind. Keep payloads JSON-serializable.
    """

    source: str
    kind: EventKind
    natural_key: str
    payload: dict[str, Any]
    occurred_at: datetime
    # Steam appid (or platform app id) this event is about, when known.
    platform_app_id: int | None = None
    fetched_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def dedup_key(self) -> str:
        return f"{self.source}:{self.kind}:{self.natural_key}"


@dataclass(slots=True)
class FetchContext:
    """Per-run state handed to a source: its persisted cursor plus knobs.

    ``cursor`` is opaque per-source checkpoint state (last appid synced, last news
    gid seen, ETag, If-Modified-Since value…). The source reads it at the start of
    ``fetch`` and returns an updated cursor via :class:`FetchResult`.
    """

    cursor: dict[str, Any] = field(default_factory=dict)
    # Soft cap on how many events a single run should emit (politeness / batching).
    limit: int | None = None


@dataclass(slots=True)
class FetchResult:
    """What a source run reports back. Events are streamed; this is the summary."""

    cursor: dict[str, Any] = field(default_factory=dict)
    events_emitted: int = 0
    # e.g. {"rate_limited": True, "not_modified": True} — for observability.
    stats: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Source(Protocol):
    """A pluggable upstream adapter.

    Adapters live in ``gamer.sources.<name>`` and are registered in the source
    registry. The scheduler calls ``fetch`` on each source's schedule, passing the
    source's persisted cursor and collecting the events it yields.
    """

    #: Stable identifier, also the key in ``source_cursors`` and logs.
    name: str

    #: Default APScheduler-style interval in seconds between runs.
    default_interval_seconds: int

    def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        """Yield normalized events. Should honour ``ctx.limit`` and never raise on
        expected upstream failures (429, 5xx, ETag-not-modified) — log and stop.
        """
        ...
