"""News-stream routes (UI_PLAN.md §3.5 / §8 UI-M4).

* ``GET /news``        → the cluster-grouped stream (full page, or the cards
  fragment when HTMX asks — like the catalog's filter/load-more).
* ``GET /api/v1/news`` → JSON twin.

All SQL lives in ``queries.news``; the ``source`` filter is validated against the
distinct-sources allowlist here (a bad value → first page, unfiltered — never
reaches SQL as an injected literal). No SQL here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from gamer.api.deps import EmptyStrToNone
from gamer.api.queries import news as news_q
from gamer.api.templating import templates

router = APIRouter()


async def _validated_source(source: str | None) -> tuple[str | None, list[str]]:
    """Return (source-or-None-if-not-in-allowlist, allowlist). One sources query."""
    allow = await news_q.news_sources()
    chosen = source if source in allow else None
    return chosen, allow


@router.get("/news", response_class=HTMLResponse)
async def news_page(
    request: Request,
    source: str | None = Query(default=None),
    game_id: Annotated[int | None, EmptyStrToNone] = None,
    cursor: str | None = Query(default=None),
) -> HTMLResponse:
    chosen, allow = await _validated_source(source)
    page = await news_q.news_stream(source=chosen, game_id=game_id, cursor=cursor)
    filters = {"source": chosen or "", "game_id": game_id}
    context = {"page": page, "sources": allow, "filters": filters}
    template = "_fragments/news_cards.html" if request.headers.get("HX-Request") else "news.html"
    return templates.TemplateResponse(request, template, context)


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
                "cluster_id": card.cluster_id,
                "similar_count": card.similar_count,
            }
            for card in page.cards
        ],
        "next_cursor": page.next_cursor,
    }
