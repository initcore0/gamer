"""DB-backed tests for genre subscriptions (M7): migration, track job, quota persist.

Marked ``@integration`` — needs a migrated Postgres at head (0005).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from gamer.catalog.genre_tracking import TRACK_TOP_N, known_genres, track_subscribed_genres
from gamer.db import session_scope
from gamer.db.models import Game, GameStats, Platform, Recommendation, StreamerPref
from gamer.scoring.service import build_context, recommend

pytestmark = pytest.mark.integration

_KEY = "genretest"
_APPID_BASE = 980000
NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


async def _cleanup() -> None:
    async with session_scope() as session:
        ids = (
            (
                await session.execute(
                    select(Game.id).where(
                        Game.platform_app_id.between(_APPID_BASE, _APPID_BASE + 999)
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await session.execute(delete(Recommendation).where(Recommendation.game_id.in_(ids)))
            await session.execute(delete(GameStats).where(GameStats.game_id.in_(ids)))
            await session.execute(delete(Game).where(Game.id.in_(ids)))
        await session.execute(delete(StreamerPref).where(StreamerPref.key == _KEY))


async def _seed(genres: list[str], *, offset: int, review: float | None) -> int:
    async with session_scope() as session:
        game = Game(
            platform=Platform.STEAM,
            platform_app_id=_APPID_BASE + offset,
            name=f"Game{offset}",
            genres=genres,
            tracked=False,
        )
        session.add(game)
        await session.flush()
        if review is not None:
            session.add(GameStats(game_id=game.id, review_count=review))
        return game.id


async def test_migration_head_has_subscribed_genres_column() -> None:
    # A pref row round-trips the new column with its default.
    await _cleanup()
    try:
        async with session_scope() as session:
            session.add(StreamerPref(key=_KEY))
        async with session_scope() as session:
            row = (
                await session.execute(select(StreamerPref).where(StreamerPref.key == _KEY))
            ).scalar_one()
            assert row.subscribed_genres == []
    finally:
        await _cleanup()


async def test_track_caps_and_case_insensitive_and_never_untracks() -> None:
    await _cleanup()
    try:
        # Games in "Puzzle" (mixed casing) with varying review counts, plus a decoy.
        gid_hi = await _seed(["Puzzle"], offset=1, review=500.0)
        gid_lo = await _seed(["PUZZLE"], offset=2, review=10.0)
        gid_none = await _seed(["puzzle"], offset=3, review=None)  # no stats
        gid_other = await _seed(["RPG"], offset=4, review=999.0)

        # Pre-track an existing puzzle game to prove "never untracks" + not counted.
        async with session_scope() as session:
            g = (await session.execute(select(Game).where(Game.id == gid_hi))).scalar_one()
            g.tracked = True

        newly = await track_subscribed_genres(["puzzle"])
        # gid_lo + gid_none newly tracked (gid_hi already tracked, not counted).
        assert newly == 2

        async with session_scope() as session:
            rows = {
                r.id: r.tracked
                for r in (
                    await session.execute(
                        select(Game).where(Game.id.in_([gid_hi, gid_lo, gid_none, gid_other]))
                    )
                ).scalars()
            }
        assert rows[gid_hi] is True  # stayed tracked
        assert rows[gid_lo] is True  # case-insensitive match
        assert rows[gid_none] is True  # statless still qualifies
        assert rows[gid_other] is False  # different genre untouched

        # Idempotent: re-running tracks nothing new.
        assert await track_subscribed_genres(["puzzle"]) == 0
    finally:
        await _cleanup()


async def test_track_reads_prefs_when_no_arg() -> None:
    await _cleanup()
    try:
        gid = await _seed(["Metroidvania"], offset=10, review=1.0)
        async with session_scope() as session:
            session.add(StreamerPref(key="default", subscribed_genres=["metroidvania"]))
        try:
            newly = await track_subscribed_genres()
            assert newly >= 1
            async with session_scope() as session:
                g = (await session.execute(select(Game).where(Game.id == gid))).scalar_one()
                assert g.tracked is True
        finally:
            async with session_scope() as session:
                await session.execute(delete(StreamerPref).where(StreamerPref.key == "default"))
    finally:
        await _cleanup()


async def test_track_cap_constant_is_bounded() -> None:
    assert TRACK_TOP_N == 200


async def test_known_genres_returns_distinct_sorted() -> None:
    await _cleanup()
    try:
        await _seed(["Zebra", "Alpha"], offset=20, review=1.0)
        genres = await known_genres()
        assert "Alpha" in genres
        assert "Zebra" in genres
        assert genres == sorted(genres)
    finally:
        await _cleanup()


async def test_build_context_loads_subscribed_genres() -> None:
    await _cleanup()
    try:
        async with session_scope() as session:
            session.add(StreamerPref(key=_KEY, subscribed_genres=["Puzzle"]))
        async with session_scope() as session:
            ctx = await build_context(session, now=NOW, key=_KEY)
        assert ctx.subscribed_genres == ["Puzzle"]
    finally:
        await _cleanup()


async def test_recommend_persists_only_displayed_picks_with_quota() -> None:
    await _cleanup()
    try:
        # 4 tracked games: 3 high-score RPG, 1 low-score Puzzle below the cut.
        rpg = [await _seed(["RPG"], offset=30 + i, review=1000.0 - i) for i in range(3)]
        puzzle = await _seed(["Puzzle"], offset=40, review=1.0)
        async with session_scope() as session:
            for gid in [*rpg, puzzle]:
                g = (await session.execute(select(Game).where(Game.id == gid))).scalar_one()
                g.tracked = True
            session.add(StreamerPref(key=_KEY, subscribed_genres=["Puzzle"]))

        recs = await recommend(limit=3, key=_KEY, now=NOW, persist=True, subscribed_quota=3)
        # Quota promoted the Puzzle pick into the displayed 3.
        assert puzzle in [r.game_id for r in recs]
        assert len(recs) == 3

        # Persisted exactly the 3 displayed picks — not the whole ranked pool.
        async with session_scope() as session:
            persisted = (
                (
                    await session.execute(
                        select(Recommendation.game_id).where(
                            Recommendation.game_id.in_([*rpg, puzzle])
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert sorted(persisted) == sorted(r.game_id for r in recs)
    finally:
        await _cleanup()
