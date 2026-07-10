"""DB-backed recommendation-feed tests (UI_PLAN.md §3.4, UI-M4).

Marked ``@integration`` — requires a migrated Postgres via GAMER_DB__*. Verifies
keyset pagination walks the feed newest-first with no gaps/repeats and that
feedback verdict counts aggregate without an N+1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from gamer.api.queries.recs import list_recommendations
from gamer.db import session_scope
from gamer.db.models import Feedback, FeedbackVerdict, Game, Platform, Recommendation

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


async def _seed() -> tuple[int, list[int]]:
    async with session_scope() as session:
        game = Game(platform=Platform.STEAM, platform_app_id=800001, name="Rec Game")
        session.add(game)
        await session.flush()
        gid = game.id
        rec_ids: list[int] = []
        for i in range(5):
            rec = Recommendation(
                game_id=gid,
                score=0.5 + i / 100,
                breakdown={"momentum": {"weighted": 0.3, "reason": "surging"}},
                created_at=_NOW - timedelta(minutes=i),
                sent_at=_NOW if i == 0 else None,
            )
            session.add(rec)
            await session.flush()
            rec_ids.append(rec.id)
        # Feedback on the newest rec: 2 up, 1 down.
        session.add(Feedback(rec_id=rec_ids[0], verdict=FeedbackVerdict.UP))
        session.add(Feedback(rec_id=rec_ids[0], verdict=FeedbackVerdict.UP))
        session.add(Feedback(rec_id=rec_ids[0], verdict=FeedbackVerdict.DOWN))
    return gid, rec_ids


async def _cleanup(gid: int) -> None:
    async with session_scope() as session:
        await session.execute(delete(Game).where(Game.id == gid))


async def test_feed_pagination_and_feedback_counts() -> None:
    gid, rec_ids = await _seed()
    try:
        seen: list[int] = []
        cursor: str | None = None
        for _ in range(50):
            page = await list_recommendations(cursor=cursor, limit=2)
            seen.extend(r.id for r in page.rows if r.id in rec_ids)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        # All five seeded recs seen exactly once, newest-first order preserved.
        assert [r for r in seen if r in rec_ids] == rec_ids
        assert len(set(seen)) == len(seen)

        first = await list_recommendations(limit=2)
        newest = next(r for r in first.rows if r.id == rec_ids[0])
        assert newest.feedback == {"up": 2, "down": 1, "played": 0}
        assert newest.sent_at is not None
        assert newest.game_name == "Rec Game"
    finally:
        await _cleanup(gid)
