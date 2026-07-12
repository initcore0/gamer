"""Game-detail JSON routes (API_CONTRACT.md §Catalog).

* ``GET /api/v1/games/{id}``          → detail + latest breakdown + news + similar.
* ``GET /api/v1/games/{id}/series``   → compact chart data (rollup-aware).

All SQL lives in ``queries.game_detail`` / ``queries.signals``; params are typed
so bad ``metric``/``range`` values 422 (never reach SQL).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse

from gamer.api.deps import EmptyStrToNone
from gamer.api.queries import game_detail as detail_q
from gamer.api.queries import signals as signals_q
from gamer.api.queries.signals import SeriesMetric, SeriesRange

router = APIRouter()

_SERIES_CACHE = "public, max-age=300"


@router.get("/api/v1/games/{game_id}")
async def game_detail_json(game_id: int) -> dict[str, Any]:
    detail = await detail_q.game_detail(game_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="game not found")
    breakdown = await detail_q.latest_breakdown(game_id)
    news = await detail_q.game_news(game_id)
    similar = await detail_q.similar_games(game_id)
    return {
        "id": detail.id,
        "name": detail.name,
        "platform": detail.platform,
        "platform_app_id": detail.platform_app_id,
        "genres": detail.genres,
        "release_date": detail.release_date.isoformat() if detail.release_date else None,
        "price_cents": detail.price_cents,
        "is_free": detail.is_free,
        "tracked": detail.tracked,
        "current_players": detail.current_players,
        "players_24h_delta": detail.players_24h_delta,
        "review_count": detail.review_count,
        "twitch_viewers": detail.twitch_viewers,
        "last_signal_at": detail.last_signal_at.isoformat() if detail.last_signal_at else None,
        "steam_url": _steam_store_url(detail),
        "breakdown": None
        if breakdown is None
        else {
            "score": breakdown.score,
            "breakdown": breakdown.breakdown,
            "created_at": breakdown.created_at.isoformat() if breakdown.created_at else None,
        },
        "news": [
            {
                "id": card.id,
                "title": card.title,
                "url": card.url,
                "source": card.source,
                "published_at": card.published_at.isoformat() if card.published_at else None,
            }
            for card in news
        ],
        "similar": [
            {
                "id": sim.id,
                "name": sim.name,
                "genres": list(getattr(sim, "genres", []) or []),
                "current_players": getattr(sim, "current_players", None),
            }
            for sim in similar
        ],
    }


@router.get("/api/v1/games/{game_id}/series")
async def game_series_json(
    game_id: int,
    metric: SeriesMetric = Query(...),
    range: Annotated[SeriesRange | None, EmptyStrToNone] = SeriesRange.D7,
) -> Response:
    detail = await detail_q.game_detail(game_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="game not found")
    result = await signals_q.series(game_id, metric, range or SeriesRange.D7)
    return JSONResponse(
        {"ts": result.ts, "values": result.values},
        headers={"Cache-Control": _SERIES_CACHE},
    )


def _steam_store_url(detail: detail_q.GameDetail) -> str | None:
    """Steam store link for a steam-platform game (the SPA renders it as a button)."""
    if detail.platform == "steam":
        return f"https://store.steampowered.com/app/{detail.platform_app_id}"
    return None
