"""Rollup-writer job — 1d signal buckets (UI_PLAN.md §5.5, §8 UI-M3).

Chart ranges beyond 7 days read pre-aggregated ``signals_rollups`` rather than
raw ``signals_samples`` (§5.5). This job fills the ``window="1d"`` buckets: one
row per (game, metric, UTC-midnight bucket_start) carrying avg/min/max and the
sample count, computed with **set-based SQL** (``INSERT ... SELECT
date_trunc('day', ts) ... GROUP BY ... ON CONFLICT DO UPDATE``) — never per-row
Python over a multi-million-row table.

**Incremental high-water.** A full recompute every run would rescan all history.
Instead we track the newest existing 1d ``bucket_start`` per metric and recompute
only buckets ``>=`` that instant. That still updates the *current* (newest)
bucket as late samples land in it — the upsert on ``uq_rollup`` overwrites the
partially-aggregated row — while skipping the settled past. The first ever run
(no rollups yet) has no high-water and aggregates everything.

Registered as ``rollups:refresh`` hourly (see :mod:`gamer.jobs`).

Design mirrors the sibling stats job: the pure decision helper
(:func:`recompute_floor`) is unit-tested DB-free; the SQL pass
(:func:`refresh_rollups`) is integration-marked.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from gamer.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("signals.rollups")

WINDOW_1D = "1d"


def recompute_floor(newest_bucket_start: datetime | None) -> datetime | None:
    """The oldest ``bucket_start`` to recompute given the current high-water.

    Returns ``newest_bucket_start`` — recompute that bucket and everything newer
    so late samples in the current bucket update it — or ``None`` (no rollups
    yet) meaning "aggregate all history". Pure; the DB pass reads the high-water
    and passes it here so the incremental boundary is unit-testable.
    """
    return newest_bucket_start


async def _newest_bucket_start(session: AsyncSession) -> datetime | None:
    """Newest existing 1d ``bucket_start`` across all (game, metric) rollups."""
    from sqlalchemy import func, select

    from gamer.db.models import SignalRollup

    result: datetime | None = await session.scalar(
        select(func.max(SignalRollup.bucket_start)).where(SignalRollup.window == WINDOW_1D)
    )
    return result


async def refresh_rollups() -> int:
    """Fill/refresh ``window="1d"`` rollups from raw samples (set-based upsert).

    Aggregates ``signals_samples`` into per-(game, metric) UTC-day buckets with
    ``date_trunc('day', ts)``, restricted to buckets ``>=`` the incremental floor
    (:func:`recompute_floor`), and upserts on ``uq_rollup``. Returns the number of
    (game, metric, bucket) rows written. Talks to the database → integration-only.
    """
    from sqlalchemy import bindparam, func, literal, select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from gamer.db import session_scope
    from gamer.db.models import SignalRollup, SignalSample

    bucket = func.date_trunc("day", SignalSample.ts).label("bucket_start")

    async with session_scope() as session:
        floor = recompute_floor(await _newest_bucket_start(session))

        select_stmt = select(
            SignalSample.game_id,
            SignalSample.metric,
            literal(WINDOW_1D),
            bucket,
            func.avg(SignalSample.value),
            func.min(SignalSample.value),
            func.max(SignalSample.value),
            func.count(),
        ).group_by(SignalSample.game_id, SignalSample.metric, bucket)
        if floor is not None:
            # Recompute the current (newest) bucket and anything newer only.
            select_stmt = select_stmt.having(bucket >= bindparam("floor", floor))

        insert_stmt = pg_insert(SignalRollup).from_select(
            [
                SignalRollup.game_id,
                SignalRollup.metric,
                SignalRollup.window,
                SignalRollup.bucket_start,
                SignalRollup.avg,
                SignalRollup.min,
                SignalRollup.max,
                SignalRollup.samples,
            ],
            select_stmt,
        )
        upsert = insert_stmt.on_conflict_do_update(
            constraint="uq_rollup",
            set_={
                "avg": insert_stmt.excluded.avg,
                "min": insert_stmt.excluded.min,
                "max": insert_stmt.excluded.max,
                "samples": insert_stmt.excluded.samples,
            },
        )
        result = await session.execute(upsert)
        # execute() of a DML statement yields a CursorResult with a rowcount.
        written = int(cast("CursorResult[Any]", result).rowcount)

    log.info("rollups_refresh_done", window=WINDOW_1D, rows=written)
    return written
