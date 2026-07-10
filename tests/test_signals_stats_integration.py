"""DB-backed test for the stats-refresh upsert pass (UI_PLAN.md §5.4).

Marked ``@integration`` — needs a migrated Postgres. Seeds a game with a handful
of PLAYERS/REVIEW_COUNT samples across the 7-day window and asserts the upserted
``game_stats`` row matches what the pure helpers compute; a second run exercises
the ON CONFLICT DO UPDATE path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from gamer.db import session_scope
from gamer.db.models import Game, GameStats, Platform, SignalMetric, SignalSample
from gamer.signals.stats import refresh_game_stats

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


async def _seed() -> int:
    async with session_scope() as session:
        game = Game(platform=Platform.STEAM, platform_app_id=970001, name="StatsTest")
        session.add(game)
        await session.flush()
        gid = game.id
        samples = [
            (SignalMetric.PLAYERS, NOW - timedelta(hours=1), 300.0),
            (SignalMetric.PLAYERS, NOW - timedelta(hours=26), 100.0),
            (SignalMetric.PLAYERS, NOW - timedelta(days=5), 50.0),
            (SignalMetric.REVIEW_COUNT, NOW - timedelta(hours=2), 5000.0),
        ]
        for metric, ts, value in samples:
            session.add(SignalSample(game_id=gid, metric=metric, ts=ts, value=value))
    return gid


async def _cleanup(gid: int) -> None:
    async with session_scope() as session:
        await session.execute(delete(GameStats).where(GameStats.game_id == gid))
        await session.execute(delete(Game).where(Game.id == gid))


async def _fetch(gid: int) -> GameStats | None:
    async with session_scope() as session:
        return (
            await session.execute(select(GameStats).where(GameStats.game_id == gid))
        ).scalar_one_or_none()


async def test_refresh_upserts_expected_stats() -> None:
    gid = await _seed()
    try:
        await refresh_game_stats(now=NOW)
        row = await _fetch(gid)
        assert row is not None
        assert row.current_players == 300.0
        # baseline = latest sample ≥24h old = 100 (26h ago) → delta 200.
        assert row.players_24h_delta == 200.0
        assert row.review_count == 5000.0
        assert row.players_7d_spark  # non-empty sparkline
        assert row.last_signal_at is not None

        # Second run must UPDATE (ON CONFLICT), not error or duplicate.
        await refresh_game_stats(now=NOW)
        row2 = await _fetch(gid)
        assert row2 is not None
        assert row2.current_players == 300.0
    finally:
        await _cleanup(gid)
