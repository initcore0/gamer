"""DB-backed sources ops-view tests (UI_PLAN.md §3.6, UI-M4).

Marked ``@integration`` — requires a migrated Postgres via GAMER_DB__*. Verifies
per-source overview (timestamps, stale flag, recent jobs with truncated errors)
and the per-day event counts merge.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from gamer.api.queries.sources import events_per_day, source_overview
from gamer.db import session_scope
from gamer.db.models import Game, Job, JobStatus, NewsItem, Platform, SourceCursor

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
_SRC = "uisrc_test"


async def _cleanup(gid: int | None = None) -> None:
    async with session_scope() as session:
        await session.execute(delete(Job).where(Job.name == f"source:{_SRC}"))
        await session.execute(delete(SourceCursor).where(SourceCursor.source == _SRC))
        if gid is not None:
            await session.execute(delete(NewsItem).where(NewsItem.game_id == gid))
            await session.execute(delete(Game).where(Game.id == gid))


async def test_source_overview_stale_and_jobs() -> None:
    await _cleanup()
    async with session_scope() as session:
        # Never succeeded, last run long ago → stale.
        session.add(
            SourceCursor(
                source=_SRC,
                cursor={},
                last_run_at=_NOW - timedelta(days=3),
                last_success_at=None,
            )
        )
        session.add(
            Job(
                name=f"source:{_SRC}",
                status=JobStatus.ERROR,
                started_at=_NOW - timedelta(hours=1),
                finished_at=_NOW - timedelta(hours=1) + timedelta(seconds=3),
                detail={"emitted": 12, "written": 4},
                error="X" * 500,
            )
        )
    try:
        cards = await source_overview(now=_NOW)
        card = next(c for c in cards if c.source == _SRC)
        assert card.stale is True
        assert len(card.jobs) == 1
        job = card.jobs[0]
        assert job.status == "error"
        assert job.emitted == 12 and job.written == 4
        assert job.duration_s == pytest.approx(3.0)
        # Error truncated server-side to 200 chars + ellipsis (§7).
        assert job.error is not None and len(job.error) == 201
    finally:
        await _cleanup()


async def test_events_per_day_merges_counts() -> None:
    async with session_scope() as session:
        game = Game(platform=Platform.STEAM, platform_app_id=820001, name="Ev Game")
        session.add(game)
        await session.flush()
        gid = game.id
        session.add(
            NewsItem(
                source="pcgamer",
                external_id="ev_news_1",
                game_id=gid,
                title="t",
                published_at=_NOW - timedelta(days=1),
            )
        )
    try:
        days = await events_per_day(days=14, now=_NOW)
        assert len(days) == 14
        assert days[0].day < days[-1].day  # oldest first
        # Each row has the three merged counts as ints.
        assert all(isinstance(d.samples, int) for d in days)
        assert sum(d.news for d in days) >= 1  # our news item lands in the window
    finally:
        await _cleanup(gid)
