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

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def add_interval_job(self, fn: JobFn, *, seconds: int, name: str) -> None:
        self._scheduler.add_job(fn, "interval", seconds=seconds, id=name, name=name)
        log.info("job_registered", name=name, interval_seconds=seconds)

    def start(self) -> None:
        self._scheduler.start()
        log.info("scheduler_started", jobs=len(self._scheduler.get_jobs()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
