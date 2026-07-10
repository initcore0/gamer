"""Game-detail routes (UI_PLAN.md §3.3 / §8 UI-M3).

* ``GET /games/{id}``                 → the detail page (404 → styled 404 page).
* ``GET /api/v1/games/{id}``          → JSON twin (detail + latest breakdown).
* ``GET /api/v1/games/{id}/series``   → compact chart data (rollup-aware).

All SQL lives in ``queries.game_detail`` / ``queries.signals``; params are typed
so bad ``metric``/``range`` values 422 (never reach SQL). Charts are driven by
the static ``/static/charts.js`` reading data-* attributes — no inline scripts
(CSP: ``script-src 'self'``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from gamer.api.queries import game_detail as detail_q
from gamer.api.queries import signals as signals_q
from gamer.api.queries.signals import SeriesMetric, SeriesRange
from gamer.api.templating import templates

router = APIRouter()

_SERIES_CACHE = "public, max-age=300"


@router.get("/games/{game_id}", response_class=HTMLResponse)
async def game_detail_page(request: Request, game_id: int) -> HTMLResponse:
    detail = await detail_q.game_detail(game_id)
    if detail is None:
        return templates.TemplateResponse(
            request, "game_404.html", {"game_id": game_id}, status_code=404
        )
    breakdown = await detail_q.latest_breakdown(game_id)
    news = await detail_q.game_news(game_id)
    similar = await detail_q.similar_games(game_id)
    context = {
        "game": detail,
        "breakdown": breakdown,
        "bars": _breakdown_bars(breakdown),
        "penalties": _breakdown_penalties(breakdown),
        "news": news,
        "similar": similar,
        "store_url": _steam_store_url(detail),
    }
    return templates.TemplateResponse(request, "game_detail.html", context)


@router.get("/api/v1/games/{game_id}")
async def game_detail_json(game_id: int) -> dict[str, Any]:
    detail = await detail_q.game_detail(game_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="game not found")
    breakdown = await detail_q.latest_breakdown(game_id)
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
        "breakdown": None
        if breakdown is None
        else {
            "score": breakdown.score,
            "breakdown": breakdown.breakdown,
            "created_at": breakdown.created_at.isoformat() if breakdown.created_at else None,
        },
    }


@router.get("/api/v1/games/{game_id}/series")
async def game_series_json(
    game_id: int,
    metric: SeriesMetric = Query(...),
    range: SeriesRange = Query(default=SeriesRange.D7),
) -> Response:
    detail = await detail_q.game_detail(game_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="game not found")
    result = await signals_q.series(game_id, metric, range)
    return JSONResponse(
        {"ts": result.ts, "values": result.values},
        headers={"Cache-Control": _SERIES_CACHE},
    )


# ── Server-side breakdown rendering helpers (UI_PLAN.md §3.3 score panel) ────


def _steam_store_url(detail: detail_q.GameDetail) -> str | None:
    """Steam store link for a steam-platform game (rel=noopener at the template)."""
    if detail.platform == "steam":
        return f"https://store.steampowered.com/app/{detail.platform_app_id}"
    return None


def _breakdown_bars(breakdown: detail_q.Breakdown | None) -> list[dict[str, Any]]:
    """Component bars: key, weighted value, reason, bar width % (max abs = 100%).

    Penalty entries (``penalty:*`` → ``{multiplier, reason}``) are excluded here —
    they are rendered separately by :func:`_breakdown_penalties`.
    """
    if breakdown is None:
        return []
    parts = [
        (key, part)
        for key, part in breakdown.breakdown.items()
        if isinstance(part, dict)
        and not key.startswith("penalty:")
        and part.get("weighted") is not None
    ]
    if not parts:
        return []
    max_abs = max(abs(float(part["weighted"])) for _key, part in parts) or 1.0
    bars: list[dict[str, Any]] = []
    for key, part in parts:
        weighted = float(part["weighted"])
        bars.append(
            {
                "key": key,
                "weighted": weighted,
                "reason": str(part.get("reason", "")),
                "width_pct": abs(weighted) / max_abs * 100.0,
                "positive": weighted >= 0.0,
            }
        )
    return bars


def _breakdown_penalties(breakdown: detail_q.Breakdown | None) -> list[dict[str, Any]]:
    """Penalty rows: key, multiplier, reason (``penalty:*`` breakdown entries)."""
    if breakdown is None:
        return []
    rows: list[dict[str, Any]] = []
    for key, part in breakdown.breakdown.items():
        if not key.startswith("penalty:") or not isinstance(part, dict):
            continue
        rows.append(
            {
                "key": key.removeprefix("penalty:"),
                "multiplier": part.get("multiplier"),
                "reason": str(part.get("reason", "")),
            }
        )
    return rows
