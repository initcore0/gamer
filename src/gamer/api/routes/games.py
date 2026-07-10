"""Catalog routes (UI_PLAN.md §3.2 / §8 UI-M1).

``GET /games`` renders the paginated list — the full page normally, or just the
rows fragment when HTMX asks (``HX-Request`` header) for search/load-more.
``GET /api/v1/games`` is the JSON twin. Both call
:func:`queries.games.list_games`; query params are parsed here, never SQL.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from gamer.api.queries import games as games_q
from gamer.api.templating import templates

router = APIRouter()


@router.get("/games", response_class=HTMLResponse)
async def games_page(
    request: Request,
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
) -> HTMLResponse:
    page = await games_q.list_games(search=q, cursor=cursor)
    context = {"page": page, "q": q}
    # HTMX search/load-more want only the rows; a normal navigation wants the
    # full page. The header check is the single fragment-vs-page switch (§4).
    template = "_fragments/game_rows.html" if request.headers.get("HX-Request") else "games.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/api/v1/games")
async def games_json(
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=games_q.DEFAULT_LIMIT, ge=1, le=200),
) -> dict[str, Any]:
    page = await games_q.list_games(search=q, cursor=cursor, limit=limit)
    return {
        "games": [
            {
                "id": row.id,
                "name": row.name,
                "platform": row.platform,
                "genres": row.genres,
                "tracked": row.tracked,
            }
            for row in page.rows
        ],
        "next_cursor": page.next_cursor,
    }
