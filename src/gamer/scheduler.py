"""APScheduler wiring (PLAN.md §2: in-process, DB-checkpointed).

M0 ships the scheduler skeleton; M1+ register the actual source-poll and rollup
jobs. Each job run is recorded in the ``jobs`` table for observability.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from gamer.logging import get_logger

log = get_logger("scheduler")

JobFn = Callable[[], Awaitable[None]]


class Scheduler:
    """Thin wrapper around APScheduler for interval jobs."""

    # APScheduler's default misfire_grace_time is 1s: if a job's fire moment lands
    # inside a CPU/GC stall or a long sibling job (e.g. an embedding batch), it is
    # silently skipped and rescheduled a full interval later — dropping an hourly
    # poll for an hour, or a daily digest for a day. A generous grace lets a
    # briefly-late job still run instead of being dropped. coalesce collapses a
    # backlog of missed fires into one run so we never stampede after a stall.
    _MISFIRE_GRACE_S = 3600

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def add_interval_job(self, fn: JobFn, *, seconds: int, name: str) -> None:
        self._scheduler.add_job(
            fn,
            "interval",
            seconds=seconds,
            id=name,
            name=name,
            misfire_grace_time=self._MISFIRE_GRACE_S,
            coalesce=True,
        )
        log.info("job_registered", name=name, interval_seconds=seconds)

    def add_daily_job(self, fn: JobFn, *, hour: int, minute: int = 0, name: str) -> None:
        """Run ``fn`` every day at a fixed UTC time (cron trigger).

        Unlike an interval job — whose first firing is one full interval after
        boot, so a daily job drifts with every restart — a cron job fires at the
        same wall-clock time regardless of when the process started.
        """
        self._scheduler.add_job(
            fn,
            "cron",
            hour=hour,
            minute=minute,
            timezone="UTC",
            id=name,
            name=name,
            misfire_grace_time=self._MISFIRE_GRACE_S,
            coalesce=True,
        )
        log.info("job_registered", name=name, daily_at=f"{hour:02d}:{minute:02d}Z")

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler_started", jobs=len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
