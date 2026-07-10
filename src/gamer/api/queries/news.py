"""News-stream query layer (UI_PLAN.md §3.5, §8 UI-M4).

``/news`` is a cluster-grouped stream across all sources, newest first, with
optional ``source`` / ``game_id`` filters. Cluster grouping reuses the pure
:func:`gamer.api.queries.game_detail.group_news_by_cluster` helper (one card per
cluster, "+N similar" folded) — the game page already uses it.

Rules (UI_PLAN.md §9): no SQL in routes; keyset pagination over
``(published_at DESC, id DESC)`` — never ``OFFSET``; ``source`` is validated
against an allowlist of distinct sources; ``game_id`` is a bound int; every value
is a bound param.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select

from gamer.api.deps import decode_cursor, encode_cursor
from gamer.api.queries.game_detail import NewsCard, NewsRef, group_news_by_cluster
from gamer.db import session_scope
from gamer.db.models import NewsItem

DEFAULT_LIMIT = 30
_MAX_LIMIT = 100

__all__ = ["NewsCard", "NewsPage", "news_sources", "news_stream"]


@dataclass(frozen=True, slots=True)
class NewsPage:
    """A page of cluster-deduped news cards plus the next-page cursor (if any)."""

    cards: list[NewsCard]
    next_cursor: str | None


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


async def news_sources() -> list[str]:
    """Distinct news sources, for the filter-bar allowlist (UI_PLAN.md §3.5).

    A small ``SELECT DISTINCT`` over ``news_items.source`` — the allowlist the
    route validates the ``source`` filter against so a bad value never hits SQL.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(select(NewsItem.source).distinct().order_by(NewsItem.source))
        ).all()
    return [str(r[0]) for r in rows]


async def news_stream(
    source: str | None = None,
    game_id: int | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> NewsPage:
    """Keyset-paginated, cluster-grouped news stream, newest first (§3.5).

    Orders by ``(published_at DESC, id DESC)``; the cursor is the ISO
    ``published_at`` plus ``id`` of the last raw row. Over-fetches ``limit * 2``
    rows so folding same-cluster stories still yields close to ``limit`` cards;
    the cursor is set from the last *raw* row so pagination has no gaps. ``source``
    / ``game_id`` are bound-param filters. Talks to the DB → integration-only.
    """
    limit = _clamp_limit(limit)
    fetch = limit * 2

    stmt = select(
        NewsItem.id,
        NewsItem.title,
        NewsItem.url,
        NewsItem.source,
        NewsItem.published_at,
        NewsItem.cluster_id,
    )

    if source is not None:
        stmt = stmt.where(NewsItem.source == source)
    if game_id is not None:
        stmt = stmt.where(NewsItem.game_id == game_id)

    seek = decode_cursor(cursor, (str, int))
    if seek is not None:
        last_iso, last_id = seek
        last_dt = _parse_dt(last_iso)
        if last_dt is not None:
            stmt = stmt.where(
                or_(
                    NewsItem.published_at < last_dt,
                    and_(NewsItem.published_at == last_dt, NewsItem.id < int(last_id)),
                )
            )

    stmt = stmt.order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(fetch + 1)

    async with session_scope() as session:
        result = (await session.execute(stmt)).all()

    has_more = len(result) > fetch
    page = result[:fetch]

    refs = [
        NewsRef(
            id=int(nid),
            title=str(title),
            url=url,
            source=str(src),
            published_at=published_at,
            cluster_id=None if cluster_id is None else int(cluster_id),
        )
        for nid, title, url, src, published_at, cluster_id in page
    ]
    cards = group_news_by_cluster(refs, limit=limit)

    # Cursor from the last raw row consumed into the page slice, so the next page
    # resumes strictly after it with no gaps. (A cluster spanning the page
    # boundary may fold across two pages — an acceptable, gap-free trade-off.)
    next_cursor: str | None = None
    if has_more and page:
        last_id, _t, _u, _s, last_published, _c = page[-1]
        next_cursor = encode_cursor((last_published.isoformat(), int(last_id)))
    return NewsPage(cards=cards, next_cursor=next_cursor)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
