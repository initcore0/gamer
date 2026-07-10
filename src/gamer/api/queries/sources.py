"""Sources ops-view query layer (UI_PLAN.md §3.6, §8 UI-M4).

``/sources`` is the pipeline-health page: per-source last run / last success and
a STALE flag (reusing :func:`gamer.health.stale_sources_from_rows` + the settings
threshold), the recent ``jobs`` rows for that source, and a 14-day event-count
bar chart (samples + news + games per day).

Rules (UI_PLAN.md §9): no SQL in routes; job error strings are already redacted
at write time and are additionally TRUNCATED server-side here (§7 — the sources
page never renders a raw/unfiltered error); every value is a bound param.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from gamer.config import Settings, get_settings
from gamer.db import session_scope
from gamer.db.models import Game, Job, NewsItem, SignalSample, SourceCursor
from gamer.health import stale_sources_from_rows

# The scheduler records source runs as jobs named ``source:{source}``.
_JOB_PREFIX = "source:"
_RECENT_JOBS = 20
_ERROR_MAX = 200
_DEFAULT_DAYS = 14


@dataclass(frozen=True, slots=True)
class JobRow:
    """One recent ``jobs`` row for a source (error truncated server-side)."""

    id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    emitted: int | None
    written: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class SourceCard:
    """Per-source ops summary: timestamps, stale flag, and recent jobs."""

    source: str
    last_run_at: datetime | None
    last_success_at: datetime | None
    stale: bool
    jobs: list[JobRow] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DayCounts:
    """Event counts for one calendar day (UTC): samples, news, games."""

    day: str
    samples: int
    news: int
    games: int


def _truncate_error(error: str | None) -> str | None:
    """Truncate an (already-redacted) job error to ``_ERROR_MAX`` chars (§7)."""
    if error is None:
        return None
    if len(error) <= _ERROR_MAX:
        return error
    return error[:_ERROR_MAX] + "…"


def _duration_s(started: datetime, finished: datetime | None) -> float | None:
    if finished is None:
        return None
    return (finished - started).total_seconds()


def _job_row(rt: Any) -> JobRow:
    job_id, status, started_at, finished_at, detail, error = rt
    detail = detail or {}
    emitted = detail.get("emitted")
    written = detail.get("written")
    return JobRow(
        id=int(job_id),
        status=status.value if hasattr(status, "value") else str(status),
        started_at=started_at,
        finished_at=finished_at,
        duration_s=_duration_s(started_at, finished_at),
        emitted=None if emitted is None else int(emitted),
        written=None if written is None else int(written),
        error=_truncate_error(error),
    )


async def source_overview(
    *, now: datetime | None = None, settings: Settings | None = None
) -> list[SourceCard]:
    """Per-source ops cards: timestamps, STALE flag, and recent jobs (§3.6).

    Reads ``source_cursors`` for the timestamps + stale detection (shared with
    the health module) and the last ``_RECENT_JOBS`` ``jobs`` rows per source
    (matched by the ``source:{name}`` job name). Talks to the DB → integration-only.
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)

    async with session_scope() as session:
        cursors = list((await session.execute(select(SourceCursor))).scalars().all())

        cards: list[SourceCard] = []
        stale = set(
            stale_sources_from_rows(
                cursors, now=now, stale_after_hours=settings.health.stale_after_hours
            )
        )
        for cur in sorted(cursors, key=lambda c: c.source):
            job_rows = (
                await session.execute(
                    select(
                        Job.id,
                        Job.status,
                        Job.started_at,
                        Job.finished_at,
                        Job.detail,
                        Job.error,
                    )
                    .where(Job.name == f"{_JOB_PREFIX}{cur.source}")
                    .order_by(Job.started_at.desc())
                    .limit(_RECENT_JOBS)
                )
            ).all()
            cards.append(
                SourceCard(
                    source=cur.source,
                    last_run_at=cur.last_run_at,
                    last_success_at=cur.last_success_at,
                    stale=cur.source in stale,
                    jobs=[_job_row(rt) for rt in job_rows],
                )
            )
    return cards


async def events_per_day(
    days: int = _DEFAULT_DAYS, *, now: datetime | None = None
) -> list[DayCounts]:
    """Samples / news / games counts per UTC day over the last ``days`` (§3.6).

    Three grouped ``date_trunc('day', …)`` counts (over ``signals_samples.ts``,
    ``news_items.published_at``, ``games.created_at``) merged in Python into one
    row per day, oldest first, with zero-filled gaps so the bar chart is dense.
    Talks to the DB → integration-only.
    """
    now = now or datetime.now(UTC)
    days = max(1, days)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    async def _grouped(ts_col: Any) -> dict[str, int]:
        bucket = func.date_trunc("day", ts_col)
        stmt = (
            select(bucket.label("d"), func.count().label("c"))
            .where(ts_col >= start)
            .group_by(bucket)
        )
        rows = (await session.execute(stmt)).all()
        return {d.date().isoformat(): int(c) for d, c in rows}

    async with session_scope() as session:
        samples = await _grouped(SignalSample.ts)
        news = await _grouped(NewsItem.published_at)
        games = await _grouped(Game.created_at)

    out: list[DayCounts] = []
    for i in range(days):
        day = (start + timedelta(days=i)).date().isoformat()
        out.append(
            DayCounts(
                day=day,
                samples=samples.get(day, 0),
                news=news.get(day, 0),
                games=games.get(day, 0),
            )
        )
    return out
