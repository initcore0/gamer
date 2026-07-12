"""Catalog routes (UI_PLAN.md §3.2 / §8 UI-M2).

``GET /games`` renders the paginated, filterable, sortable catalog — the full
page normally, or just the rows fragment when HTMX asks (``HX-Request``).
``GET /api/v1/games`` is the JSON twin. Both call
:func:`queries.games.list_games`; query params are parsed/validated here (via
FastAPI ``Enum``/``Literal`` typing so bad values 422 rather than 500), never SQL.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from gamer.api.deps import EmptyStrToNone
from gamer.api.queries import games as games_q
from gamer.api.queries.games import Sort
from gamer.api.templating import templates
from gamer.db.models import Platform

router = APIRouter()


@router.get("/games", response_class=HTMLResponse)
async def games_page(
    request: Request,
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    # NB: these use plain defaults, not Query(default=…) — FastAPI drops Annotated
    # validator metadata when the default is a Query() FieldInfo.
    platform: Annotated[Platform | None, EmptyStrToNone] = None,
    genre: str | None = Query(default=None),
    tracked: Annotated[bool | None, EmptyStrToNone] = False,
    active: Annotated[bool | None, EmptyStrToNone] = False,
    sort: Annotated[Sort | None, EmptyStrToNone] = None,
) -> HTMLResponse:
    sort = sort or Sort.NAME
    try:
        page = await games_q.list_games(
            search=q,
            cursor=cursor,
            platform=platform.value if platform else None,
            genre=genre,
            tracked_only=bool(tracked),
            active_only=bool(active),
            sort=sort.value,
        )
    except ValueError as exc:  # allowlist rejection → 422, never a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    genres = await games_q.list_genres()
    filters = {
        "q": q,
        "platform": platform.value if platform else "",
        "genre": genre or "",
        "tracked": bool(tracked),
        "active": bool(active),
        "sort": sort.value,
    }
    context = {
        "page": page,
        "q": q,
        "filters": filters,
        "genres": genres,
        "platforms": [p.value for p in Platform],
        "sorts": [s.value for s in Sort],
    }
    # HTMX filter/search/load-more want only the rows; a normal navigation wants
    # the full page. The header check is the single fragment-vs-page switch (§4).
    template = "_fragments/game_rows.html" if request.headers.get("HX-Request") else "games.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/api/v1/games")
async def games_json(
    q: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    platform: Annotated[Platform | None, EmptyStrToNone] = None,
    genre: str | None = Query(default=None),
    tracked: Annotated[bool | None, EmptyStrToNone] = False,
    active: Annotated[bool | None, EmptyStrToNone] = False,
    sort: Annotated[Sort | None, EmptyStrToNone] = None,
    limit: int = Query(default=games_q.DEFAULT_LIMIT, ge=1, le=200),
) -> dict[str, Any]:
    try:
        page = await games_q.list_games(
            search=q,
            cursor=cursor,
            limit=limit,
            platform=platform.value if platform else None,
            genre=genre,
            tracked_only=bool(tracked),
            active_only=bool(active),
            sort=(sort or Sort.NAME).value,
        )
    except ValueError as exc:  # allowlist rejection → 422, never a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "games": [
            {
                "id": row.id,
                "name": row.name,
                "platform": row.platform,
                "genres": row.genres,
                "tracked": row.tracked,
                "current_players": row.current_players,
                "players_24h_delta": row.players_24h_delta,
                "spark": row.spark,
                "review_count": row.review_count,
                "last_signal_at": row.last_signal_at.isoformat() if row.last_signal_at else None,
            }
            for row in page.rows
        ],
        "next_cursor": page.next_cursor,
    }
