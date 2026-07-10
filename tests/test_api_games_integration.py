"""DB-backed catalog query tests (UI_PLAN.md §5). Marked ``@integration`` —
deselected in CI unit runs; requires a migrated Postgres via GAMER_DB__*.

Verifies keyset pagination walks the whole catalog with no gaps/repeats and that
``ILIKE`` search (trgm index) filters by name including literal wildcards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from gamer.api.queries.games import list_games, list_genres
from gamer.db import session_scope
from gamer.db.models import Game, GameStats, Platform

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


# ── UI-M2: filters, sorts (nullable keyset), stats join, genres ──────────────


async def _seed_m2() -> list[int]:
    """Three games with genres/platforms + game_stats for two of them."""
    now = datetime.now(UTC)
    async with session_scope() as session:
        specs = [
            ("M2 Apple", Platform.STEAM, ["Action", "RPG"]),
            ("M2 Banana", Platform.XBOX, ["RPG"]),
            ("M2 Cherry", Platform.STEAM, ["Puzzle"]),
        ]
        ids: list[int] = []
        for i, (name, plat, genres) in enumerate(specs):
            g = Game(
                platform=plat,
                platform_app_id=980000 + i,
                name=name,
                genres=genres,
                tracked=(i == 0),
            )
            session.add(g)
            await session.flush()
            ids.append(g.id)
        # Apple has players+recent signal; Banana has players but stale signal;
        # Cherry has no game_stats row at all (null-tail of numeric sorts).
        session.add(
            GameStats(
                game_id=ids[0],
                current_players=500.0,
                players_24h_delta=50.0,
                players_7d_spark=[1.0, 2.0, 3.0],
                review_count=100.0,
                last_signal_at=now - timedelta(hours=1),
                updated_at=now,
            )
        )
        session.add(
            GameStats(
                game_id=ids[1],
                current_players=900.0,
                players_24h_delta=-10.0,
                players_7d_spark=[],
                review_count=None,
                last_signal_at=now - timedelta(days=30),
                updated_at=now,
            )
        )
    return ids


async def _cleanup_m2(ids: list[int]) -> None:
    async with session_scope() as session:
        await session.execute(delete(GameStats).where(GameStats.game_id.in_(ids)))
        await session.execute(delete(Game).where(Game.id.in_(ids)))


async def test_platform_and_genre_filters() -> None:
    ids = await _seed_m2()
    try:
        page = await list_games(search="M2", platform="steam")
        assert {r.name for r in page.rows} == {"M2 Apple", "M2 Cherry"}
        page = await list_games(search="M2", genre="RPG")
        assert {r.name for r in page.rows} == {"M2 Apple", "M2 Banana"}
    finally:
        await _cleanup_m2(ids)


async def test_tracked_and_active_filters() -> None:
    ids = await _seed_m2()
    try:
        tracked = await list_games(search="M2", tracked_only=True)
        assert [r.name for r in tracked.rows] == ["M2 Apple"]
        # active = signal within 7d → only Apple (Banana stale, Cherry no stats).
        active = await list_games(search="M2", active_only=True)
        assert [r.name for r in active.rows] == ["M2 Apple"]
    finally:
        await _cleanup_m2(ids)


async def test_players_sort_desc_nulls_last() -> None:
    ids = await _seed_m2()
    try:
        page = await list_games(search="M2", sort="players")
        # 900 (Banana), 500 (Apple), then NULL (Cherry) last.
        assert [r.name for r in page.rows] == ["M2 Banana", "M2 Apple", "M2 Cherry"]
    finally:
        await _cleanup_m2(ids)


async def test_players_sort_keyset_paginates_including_null_tail() -> None:
    ids = await _seed_m2()
    try:
        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            page = await list_games(search="M2", sort="players", cursor=cursor, limit=1)
            seen.extend(r.name for r in page.rows)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert seen == ["M2 Banana", "M2 Apple", "M2 Cherry"]
    finally:
        await _cleanup_m2(ids)


async def test_invalid_sort_raises() -> None:
    import pytest as _pytest

    with _pytest.raises(ValueError):
        await list_games(sort="bogus")


async def test_list_genres_distinct() -> None:
    ids = await _seed_m2()
    try:
        genres = await list_genres()
        # Apple is tracked with ["Action", "RPG"] → tracked-only path returns them.
        assert "Action" in genres
        assert "RPG" in genres
    finally:
        await _cleanup_m2(ids)
