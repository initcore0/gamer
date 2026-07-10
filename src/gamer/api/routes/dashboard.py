"""Dashboard routes (UI_PLAN.md §3.1 / §8 UI-M1).

``GET /`` renders the ported status page; ``GET /api/v1/status`` is the JSON
twin. Both call :func:`queries.status.build_status` — no SQL here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gamer.api.queries import status as status_q
from gamer.api.templating import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    payload = await status_q.build_status()
    # Top-movers strip reads the precomputed game_stats (UI_PLAN.md §5.4); it is
    # UI-only, so it stays out of the backward-compatible /status JSON payload.
    movers = await status_q.top_movers()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "status": payload,
            "stale_sources": payload["stale_sources"],
            "top_movers": movers,
        },
    )


@router.get("/api/v1/status")
async def status_json() -> status_q.StatusPayload:
    return await status_q.build_status()
