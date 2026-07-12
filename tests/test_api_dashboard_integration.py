"""DB-backed dashboard query tests (API_CONTRACT.md — /api/v1/dashboard).

Marked ``@integration`` — requires a migrated Postgres via GAMER_DB__*. Verifies
``dashboard_top_movers`` computes ``latest``/``pct`` from precomputed stats and
``dashboard_latest_recommendations`` carries id/user_key/created_at. Runnable
individually.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from gamer.api.queries.status import (
    dashboard_latest_recommendations,
    dashboard_top_movers,
)
from gamer.db import session_scope
from gamer.db.models import Game, GameStats, Platform, Recommendation

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


async def _seed() -> int:
    async with session_scope() as session:
        game = Game(platform=Platform.STEAM, platform_app_id=900777, name="Dash Game")
        session.add(game)
        await session.flush()
        gid = game.id
        session.add(
            GameStats(
                game_id=gid,
                current_players=1000.0,
                players_24h_delta=50.0,
                players_7d_spark=[1.0, 2.0],
                review_count=10.0,
                last_signal_at=_NOW,
            )
        )
        session.add(
            Recommendation(
                game_id=gid,
                pref_key="default",
                score=0.61,
                breakdown={"momentum": {"weighted": 0.2, "reason": "x"}},
                created_at=_NOW - timedelta(minutes=1),
            )
        )
    return gid


async def _cleanup(gid: int) -> None:
    async with session_scope() as session:
        await session.execute(delete(Game).where(Game.id == gid))


async def test_dashboard_top_movers_computes_latest_and_pct() -> None:
    gid = await _seed()
    try:
        movers = await dashboard_top_movers()
        mine = next(m for m in movers if m["game_id"] == gid)
        assert mine["latest"] == 1000.0
        assert mine["delta"] == 50.0
        # pct = 50 / (1000 - 50) * 100 ≈ 5.3
        assert mine["pct"] == 5.3
    finally:
        await _cleanup(gid)


async def test_dashboard_latest_recommendations_carries_context() -> None:
    gid = await _seed()
    try:
        recs = await dashboard_latest_recommendations()
        mine = next(r for r in recs if r["game_id"] == gid)
        assert mine["game_name"] == "Dash Game"
        assert mine["user_key"] == "default"
        assert mine["score"] == 0.61
        assert mine["created_at"] is not None
        assert isinstance(mine["id"], int)
    finally:
        await _cleanup(gid)
