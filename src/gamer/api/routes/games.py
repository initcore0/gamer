"""Catalog JSON routes (API_CONTRACT.md §Catalog).

* ``GET /api/v1/games``  → paginated, filterable, sortable catalog.
* ``GET /api/v1/genres`` → the canonical genre allowlist for the filter chips.

Query params are parsed/validated here (FastAPI ``Enum``/``Literal`` typing so
bad values 422 rather than 500); all SQL lives in :mod:`queries.games`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from gamer.api.deps import EmptyStrToNone
from gamer.api.queries import games as games_q
from gamer.api.queries.games import Sort
from gamer.db.models import Platform

router = APIRouter()


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


@router.get(
    "/api/v1/genres",
    tags=["catalog"],
    summary="Canonical genre list",
    response_model=None,
)
async def genres_json() -> dict[str, list[str]]:
    """The catalog's canonical genres, sorted — the ``genre`` filter allowlist."""
    return {"genres": await games_q.list_genres()}
