"""News-stream JSON routes (API_CONTRACT.md §News).

* ``GET /api/v1/news``         → the cluster-grouped stream.
* ``GET /api/v1/news/sources`` → the source-filter allowlist.

All SQL lives in ``queries.news``; the ``source`` filter is validated against the
distinct-sources allowlist here (a bad value → first page, unfiltered — never
reaches SQL as an injected literal). No SQL here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from gamer.api.deps import EmptyStrToNone
from gamer.api.queries import news as news_q

router = APIRouter()


async def _validated_source(source: str | None) -> tuple[str | None, list[str]]:
    """Return (source-or-None-if-not-in-allowlist, allowlist). One sources query."""
    allow = await news_q.news_sources()
    chosen = source if source in allow else None
    return chosen, allow


@router.get("/api/v1/news")
async def news_json(
    source: str | None = Query(default=None),
    game_id: Annotated[int | None, EmptyStrToNone] = None,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=news_q.DEFAULT_LIMIT, ge=1, le=100),
) -> dict[str, Any]:
    chosen, _allow = await _validated_source(source)
    page = await news_q.news_stream(source=chosen, game_id=game_id, cursor=cursor, limit=limit)
    return {
        "news": [
            {
                "id": card.id,
                "title": card.title,
                "url": card.url,
                "source": card.source,
                "published_at": card.published_at.isoformat() if card.published_at else None,
                # The cluster-deduped stream card doesn't carry the game join;
                # the contract keeps these fields present (null) for the SPA.
                "game_id": getattr(card, "game_id", None),
                "game_name": getattr(card, "game_name", None),
                "cluster_id": card.cluster_id,
                "similar_count": card.similar_count,
                "similar": [
                    {
                        "id": ref.id,
                        "title": ref.title,
                        "url": ref.url,
                        "source": ref.source,
                    }
                    for ref in card.similar
                ],
            }
            for card in page.cards
        ],
        "next_cursor": page.next_cursor,
    }


@router.get(
    "/api/v1/news/sources",
    tags=["news"],
    summary="News source allowlist",
    response_model=None,
)
async def news_sources_json() -> dict[str, list[str]]:
    """The distinct news sources — the ``source`` filter allowlist."""
    return {"sources": await news_q.news_sources()}
