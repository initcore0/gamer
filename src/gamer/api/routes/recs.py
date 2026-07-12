"""Recommendation-feed routes (UI_PLAN.md §3.4 / §8 UI-M4).

* ``GET /recommendations``        → the run-grouped feed (full page, or the rows
  fragment when HTMX asks via ``HX-Request`` — like the catalog's load-more).
* ``GET /api/v1/recommendations`` → JSON twin.

All SQL lives in ``queries.recs``; this module only parses params and shapes the
score-bar rendering (shared with the game-detail score panel). No SQL here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from gamer.api.queries import recs as recs_q
from gamer.api.queries.game_detail import Breakdown
from gamer.api.routes.game_detail import _breakdown_bars, _breakdown_penalties
from gamer.api.templating import templates

router = APIRouter()


def _bars_for(row: recs_q.RecRow) -> dict[str, Any]:
    """Reuse the game-detail breakdown shaping for one feed row's expander."""
    bd = Breakdown(score=row.score, breakdown=row.breakdown, created_at=row.created_at)
    return {"bars": _breakdown_bars(bd), "penalties": _breakdown_penalties(bd)}


@router.get("/recommendations", response_class=HTMLResponse)
async def recommendations_page(
    request: Request,
    cursor: str | None = Query(default=None),
) -> HTMLResponse:
    page = await recs_q.list_recommendations(cursor=cursor)
    groups = recs_q.group_runs(page.rows)
    context = {
        "page": page,
        "groups": groups,
        "bars_by_rec": {row.id: _bars_for(row) for row in page.rows},
    }
    template = (
        "_fragments/rec_rows.html" if request.headers.get("HX-Request") else "recommendations.html"
    )
    return templates.TemplateResponse(request, template, context)


@router.get("/api/v1/recommendations")
async def recommendations_json(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=recs_q.DEFAULT_LIMIT, ge=1, le=100),
) -> dict[str, Any]:
    page = await recs_q.list_recommendations(cursor=cursor, limit=limit)
    return {
        "recommendations": [
            {
                "id": row.id,
                "game_id": row.game_id,
                "game_name": row.game_name,
                "score": row.score,
                "user_key": row.user_key,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                "feedback": row.feedback,
                "breakdown": row.breakdown,
            }
            for row in page.rows
        ],
        "next_cursor": page.next_cursor,
    }
