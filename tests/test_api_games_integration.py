"""DB-backed catalog query tests (UI_PLAN.md §5). Marked ``@integration`` —
deselected in CI unit runs; requires a migrated Postgres via GAMER_DB__*.

Verifies keyset pagination walks the whole catalog with no gaps/repeats and that
``ILIKE`` search (trgm index) filters by name including literal wildcards.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from gamer.api.queries.games import list_games
from gamer.db import session_scope
from gamer.db.models import Game, Platform

pytestmark = pytest.mark.integration

_NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "50% Off Sale", "under_score"]


async def _seed() -> list[int]:
    ids: list[int] = []
    async with session_scope() as session:
        for i, name in enumerate(_NAMES):
            game = Game(platform=Platform.STEAM, platform_app_id=900000 + i, name=name)
            session.add(game)
            await session.flush()
            ids.append(game.id)
    return ids


async def _cleanup(ids: list[int]) -> None:
    async with session_scope() as session:
        await session.execute(delete(Game).where(Game.id.in_(ids)))


async def test_keyset_pagination_walks_all_rows() -> None:
    ids = await _seed()
    try:
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(100):  # guard against an infinite loop
            page = await list_games(cursor=cursor, limit=2)
            seen.extend(r.name for r in page.rows)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        # Every seeded name appears exactly once, in sorted order.
        for name in _NAMES:
            assert seen.count(name) == 1
        seeded_in_order = [n for n in seen if n in _NAMES]
        assert seeded_in_order == sorted(_NAMES)
    finally:
        await _cleanup(ids)


async def test_search_filters_by_name() -> None:
    ids = await _seed()
    try:
        page = await list_games(search="charl")
        names = [r.name for r in page.rows]
        assert "Charlie" in names
        assert "Alpha" not in names
    finally:
        await _cleanup(ids)


async def test_search_escapes_wildcards() -> None:
    ids = await _seed()
    try:
        # '%' must match literally (not "any string"), so only "50% Off Sale".
        page = await list_games(search="50%")
        names = [r.name for r in page.rows]
        assert names == ["50% Off Sale"]
        # '_' must match literally too.
        page2 = await list_games(search="under_")
        assert [r.name for r in page2.rows] == ["under_score"]
    finally:
        await _cleanup(ids)
