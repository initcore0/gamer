"""DB-backed test for the top-charts tracking upsert (feat/auto-track).

Marked ``@integration`` — needs a migrated Postgres. Verifies that a GAME event
carrying only ``{"tracked": True}`` (as the top-charts phase emits, with no name)
sets ``tracked`` without clobbering the game's existing name.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from gamer.db import session_scope
from gamer.db.models import Game, Platform
from gamer.sources.base import EventKind, RawEvent
from gamer.sources.sink import DbEventSink

pytestmark = pytest.mark.integration

_APPID = 970101
NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


async def _cleanup() -> None:
    async with session_scope() as session:
        await session.execute(
            delete(Game).where(Game.platform == Platform.STEAM, Game.platform_app_id == _APPID)
        )


async def _fetch() -> Game | None:
    async with session_scope() as session:
        return (
            await session.execute(
                select(Game).where(Game.platform == Platform.STEAM, Game.platform_app_id == _APPID)
            )
        ).scalar_one_or_none()


async def test_tracked_event_sets_flag_without_clobbering_name() -> None:
    await _cleanup()
    try:
        async with session_scope() as session:
            session.add(
                Game(platform=Platform.STEAM, platform_app_id=_APPID, name="Real Game Name")
            )

        sink = DbEventSink()
        event = RawEvent(
            source="steam_api",
            kind=EventKind.GAME,
            natural_key=str(_APPID),
            payload={"tracked": True},
            occurred_at=NOW,
            platform_app_id=_APPID,
        )
        await sink.persist([event])

        row = await _fetch()
        assert row is not None
        assert row.tracked is True
        assert row.name == "Real Game Name"  # not overwritten by the stub fallback
    finally:
        await _cleanup()
