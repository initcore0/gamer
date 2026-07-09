"""Ingestion runner — the bridge between a :class:`Source` and the database.

Responsibilities (the M1 framework contract, PLAN.md §8):
  * load the source's persisted cursor,
  * run ``source.fetch`` and collect events (honouring a limit),
  * hand events to a persistence sink (upsert by natural key = idempotency),
  * write back the updated cursor,
  * record a ``jobs`` row and update ``source_cursors`` timestamps.

Per-source adapters never touch the DB directly for cursors/jobs — they just
yield :class:`RawEvent`. This keeps every adapter a pure, testable function of
its inputs. The event *persistence* sink (how a RawEvent becomes catalog/signals
rows) is pluggable so M1 source agents and the catalog/signals owners can evolve
independently.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import Job, JobStatus, SourceCursor
from gamer.logging import get_logger, redact_secrets
from gamer.sources.base import FetchContext, RawEvent, Source

log = get_logger("sources.runner")


class EventSink(Protocol):
    """Persists a batch of RawEvents. Implemented by catalog/signals/news owners.

    Must be idempotent: re-persisting the same event (same ``dedup_key``) is a
    no-op / upsert. The runner may call it multiple times per run.
    """

    async def persist(self, events: Sequence[RawEvent]) -> int:
        """Persist events, returning how many were newly written."""
        ...


class NullSink:
    """Default sink for M0/tests — counts events without persisting them."""

    async def persist(self, events: Sequence[RawEvent]) -> int:
        return 0


async def _load_cursor(source_name: str) -> dict[str, Any]:
    async with session_scope() as session:
        row = await session.get(SourceCursor, source_name)
        return dict(row.cursor) if row else {}


async def _save_cursor(source_name: str, cursor: dict[str, Any], *, success: bool) -> None:
    now = datetime.now(UTC)
    async with session_scope() as session:
        row = await session.get(SourceCursor, source_name)
        if row is None:
            row = SourceCursor(source=source_name, cursor=cursor)
            session.add(row)
        else:
            row.cursor = cursor
        row.last_run_at = now
        if success:
            row.last_success_at = now


async def run_source(
    source: Source,
    sink: EventSink | None = None,
    *,
    limit: int | None = None,
    batch_size: int = 500,
) -> Job:
    """Run one source once, persist its events, and checkpoint. Never raises for
    expected upstream failures — records the error on the Job instead.
    """
    sink = sink or NullSink()
    name = source.name
    log.info("source_run_start", source=name)

    job = Job(name=f"source:{name}", status=JobStatus.RUNNING, detail={})
    async with session_scope() as session:
        session.add(job)
        await session.flush()
        job_id = job.id

    cursor = await _load_cursor(name)
    ctx = FetchContext(cursor=cursor, limit=limit)

    emitted = 0
    written = 0
    error: str | None = None
    batch: list[RawEvent] = []

    try:
        async for event in source.fetch(ctx):
            batch.append(event)
            emitted += 1
            if len(batch) >= batch_size:
                written += await sink.persist(batch)
                batch.clear()
            if limit is not None and emitted >= limit:
                break
        if batch:
            written += await sink.persist(batch)
    except Exception as exc:  # a broken source degrades, never crashes the app
        # httpx errors embed the full request URL (which may carry an API key);
        # redact before this string reaches logs or the jobs table.
        error = redact_secrets(f"{type(exc).__name__}: {exc}")
        log.error("source_run_failed", source=name, error=error)

    success = error is None
    # The source updates ctx.cursor in place as it advances; persist it either way
    # so partial progress is not lost.
    await _save_cursor(name, dict(ctx.cursor), success=success)

    async with session_scope() as session:
        db_job = await session.get(Job, job_id)
        assert db_job is not None
        db_job.status = JobStatus.OK if success else JobStatus.ERROR
        db_job.finished_at = datetime.now(UTC)
        db_job.detail = {"emitted": emitted, "written": written}
        db_job.error = error

    log.info(
        "source_run_done",
        source=name,
        emitted=emitted,
        written=written,
        ok=success,
    )

    # Return a detached snapshot for the caller/scheduler.
    return Job(
        id=job_id,
        name=f"source:{name}",
        status=JobStatus.OK if success else JobStatus.ERROR,
        detail={"emitted": emitted, "written": written},
        error=error,
    )


async def latest_source_status() -> list[dict[str, Any]]:
    """Small helper for the future status page: last run per source."""
    async with session_scope() as session:
        rows = (await session.execute(select(SourceCursor))).scalars().all()
        return [
            {
                "source": r.source,
                "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
                "last_success_at": (r.last_success_at.isoformat() if r.last_success_at else None),
            }
            for r in rows
        ]
