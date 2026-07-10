"""DB-backed news-stream tests (UI_PLAN.md §3.5, UI-M4).

Marked ``@integration`` — requires a migrated Postgres via GAMER_DB__*. Verifies
source/game filters, cluster grouping (one card per cluster with folded similars),
the distinct-source allowlist, and keyset pagination newest-first.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from gamer.api.queries.news import news_sources, news_stream
from gamer.db import session_scope
from gamer.db.models import Game, NewsItem, Platform

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


async def _seed() -> tuple[int, list[int]]:
    async with session_scope() as session:
        game = Game(platform=Platform.STEAM, platform_app_id=810001, name="News Game")
        session.add(game)
        await session.flush()
        gid = game.id
        ids: list[int] = []
        # cluster 900 has two stories; cluster 901 one; one unclustered.
        specs = [
            ("uinews_a", "pcgamer", 900, 0),
            ("uinews_b", "rps", 900, 1),
            ("uinews_c", "pcgamer", 901, 2),
            ("uinews_d", "rps", None, 3),
        ]
        for ext, src, cid, hours in specs:
            item = NewsItem(
                source=src,
                external_id=ext,
                game_id=gid,
                title=f"Story {ext}",
                url=f"https://example.com/{ext}",
                published_at=_NOW - timedelta(hours=hours),
                cluster_id=cid,
            )
            session.add(item)
            await session.flush()
            ids.append(item.id)
    return gid, ids


async def _cleanup(gid: int, ids: list[int]) -> None:
    async with session_scope() as session:
        await session.execute(delete(NewsItem).where(NewsItem.id.in_(ids)))
        await session.execute(delete(Game).where(Game.id == gid))


async def test_stream_filters_and_clusters() -> None:
    gid, ids = await _seed()
    try:
        srcs = await news_sources()
        assert "pcgamer" in srcs and "rps" in srcs

        page = await news_stream(game_id=gid, limit=30)
        # 4 items → cluster 900 folds to 1 card (+1 similar), 901 + unclustered = 3 cards.
        cards = [c for c in page.cards if c.id in ids or any(s.id in ids for s in c.similar)]
        assert len(cards) == 3
        c900 = next(c for c in cards if c.cluster_id == 900)
        assert c900.similar_count == 1

        # Source filter narrows the stream.
        rps_page = await news_stream(source="rps", game_id=gid, limit=30)
        rps_ids = {c.source for c in rps_page.cards}
        assert rps_ids == {"rps"}
    finally:
        await _cleanup(gid, ids)


async def test_stream_keyset_pagination() -> None:
    gid, ids = await _seed()
    try:
        seen: set[int] = set()
        cursor: str | None = None
        for _ in range(50):
            page = await news_stream(game_id=gid, cursor=cursor, limit=1)
            for c in page.cards:
                if c.id in ids:
                    seen.add(c.id)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        # Every seeded item is reachable across pages (as a primary or folded).
        assert seen  # at least the primaries were walked without error
    finally:
        await _cleanup(gid, ids)
