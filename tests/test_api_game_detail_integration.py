"""DB-backed game-detail + rollup + series tests (UI_PLAN.md §3.3, §5.5).

Marked ``@integration`` — requires a migrated Postgres (0001→0004) via GAMER_DB__*.
Covers: rollup refresh incl. idempotent re-run; series raw-vs-rollup ranges;
similar_games returns neighbours after the embed step; game_detail + news.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from gamer.api.queries.game_detail import game_detail, game_news, similar_games
from gamer.api.queries.signals import SeriesMetric, SeriesRange, series
from gamer.db import session_scope
from gamer.db.models import Game, NewsItem, Platform, SignalMetric, SignalRollup, SignalSample
from gamer.signals.rollups import refresh_rollups
from gamer.signals.stats import embed_missing_game_embeddings

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


async def _seed_game(app_id: int, name: str, genres: list[str] | None = None) -> int:
    async with session_scope() as session:
        g = Game(
            platform=Platform.STEAM,
            platform_app_id=app_id,
            name=name,
            genres=genres or ["Action"],
        )
        session.add(g)
        await session.flush()
        return g.id


async def _cleanup(ids: list[int]) -> None:
    async with session_scope() as session:
        await session.execute(delete(SignalSample).where(SignalSample.game_id.in_(ids)))
        await session.execute(delete(SignalRollup).where(SignalRollup.game_id.in_(ids)))
        await session.execute(delete(NewsItem).where(NewsItem.game_id.in_(ids)))
        await session.execute(delete(Game).where(Game.id.in_(ids)))


async def test_game_detail_and_news() -> None:
    gid = await _seed_game(700001, "Detail Game")
    try:
        async with session_scope() as session:
            for i, cid in enumerate([1, 1, 2]):
                session.add(
                    NewsItem(
                        source=f"s{i}",
                        external_id=f"e{i}",
                        game_id=gid,
                        title=f"Story {i}",
                        published_at=_NOW - timedelta(hours=i),
                        cluster_id=cid,
                    )
                )
        detail = await game_detail(gid)
        assert detail is not None
        assert detail.name == "Detail Game"
        assert detail.platform == "steam"

        cards = await game_news(gid)
        # Cluster 1 has two stories (one folded), cluster 2 one → 2 cards.
        assert len(cards) == 2
        assert cards[0].similar_count == 1
    finally:
        await _cleanup([gid])


async def test_missing_game_detail_none() -> None:
    assert await game_detail(-1) is None


async def _add_players(gid: int, points: list[tuple[datetime, float]]) -> None:
    async with session_scope() as session:
        for ts, val in points:
            session.add(SignalSample(game_id=gid, metric=SignalMetric.PLAYERS, ts=ts, value=val))


async def test_rollup_refresh_idempotent_and_series() -> None:
    gid = await _seed_game(700002, "Rollup Game")
    try:
        # Two days of samples, two per day.
        d0 = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
        await _add_players(
            gid,
            [
                (d0 + timedelta(hours=1), 10.0),
                (d0 + timedelta(hours=13), 20.0),
                (d0 + timedelta(days=1, hours=1), 30.0),
                (d0 + timedelta(days=1, hours=13), 50.0),
            ],
        )
        first = await refresh_rollups()
        assert first >= 2  # at least two day-buckets for this game

        async with session_scope() as session:
            buckets = (
                await session.execute(
                    SignalRollup.__table__.select().where(SignalRollup.game_id == gid)
                )
            ).all()
        by_start = {r.bucket_start: r for r in buckets}
        assert len(by_start) == 2
        day0 = by_start[d0]
        assert day0.avg == pytest.approx(15.0)  # (10+20)/2
        assert day0.min == 10.0 and day0.max == 20.0 and day0.samples == 2

        # Idempotent re-run: same bucket rows, avg unchanged.
        await refresh_rollups()
        async with session_scope() as session:
            buckets2 = (
                await session.execute(
                    SignalRollup.__table__.select().where(SignalRollup.game_id == gid)
                )
            ).all()
        assert len(buckets2) == 2

        # Series: 30d/all reads rollups (avg); 7d/24h reads raw samples.
        s_all = await series(gid, SeriesMetric.PLAYERS, SeriesRange.ALL, now=_NOW)
        assert s_all.values == pytest.approx([15.0, 40.0])
        assert all(isinstance(t, int) for t in s_all.ts)

        s_raw = await series(gid, SeriesMetric.PLAYERS, SeriesRange.D7, now=d0 + timedelta(days=2))
        # 7d window from d0+2d covers all 4 raw samples.
        assert s_raw.values == pytest.approx([10.0, 20.0, 30.0, 50.0])
    finally:
        await _cleanup([gid])


async def test_late_sample_updates_current_bucket() -> None:
    gid = await _seed_game(700003, "Late Game")
    try:
        d0 = datetime(2026, 7, 5, 0, 0, tzinfo=UTC)
        await _add_players(gid, [(d0 + timedelta(hours=1), 100.0)])
        await refresh_rollups()
        # A late sample lands in the SAME (newest) day bucket.
        await _add_players(gid, [(d0 + timedelta(hours=5), 300.0)])
        await refresh_rollups()
        async with session_scope() as session:
            row = (
                await session.execute(
                    SignalRollup.__table__.select()
                    .where(SignalRollup.game_id == gid)
                    .where(SignalRollup.bucket_start == d0)
                )
            ).one()
        assert row.avg == pytest.approx(200.0)  # (100+300)/2 — bucket recomputed
        assert row.samples == 2
    finally:
        await _cleanup([gid])


async def test_similar_games_after_embed_backfill() -> None:
    ids = [
        await _seed_game(700010, "Alpha Similar", ["RPG"]),
        await _seed_game(700011, "Beta Similar", ["RPG"]),
        await _seed_game(700012, "Gamma Similar", ["Puzzle"]),
    ]
    try:
        # Before embedding, similar_games returns [] (no vector yet).
        assert await similar_games(ids[0]) == []
        embedded = await embed_missing_game_embeddings(limit=200)
        assert embedded >= 3
        neighbours = await similar_games(ids[0], limit=6)
        assert neighbours  # HNSW returns the other embedded games
        assert ids[0] not in {n.id for n in neighbours}  # self excluded
    finally:
        await _cleanup(ids)
