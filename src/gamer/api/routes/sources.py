"""Sources ops-view routes (UI_PLAN.md §3.6 / §8 UI-M4).

* ``GET /sources``        → per-source cards (jobs table) + the 14-day event bar
  chart. Always a full page — no HTMX fragments here.
* ``GET /api/v1/sources`` → JSON twin.

All SQL lives in ``queries.sources``; job error strings are already redacted at
write time and truncated there (§7). No SQL here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gamer.api.queries import sources as sources_q
from gamer.api.templating import templates

router = APIRouter()


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request) -> HTMLResponse:
    cards = await sources_q.source_overview()
    events = await sources_q.events_per_day()
    context = {
        "cards": cards,
        "events": events,
        # Float-only series for the |bars svg helper (samples dominate the scale).
        "event_bars": [float(d.samples) for d in events],
    }
    return templates.TemplateResponse(request, "sources.html", context)


@router.get("/api/v1/sources")
async def sources_json() -> dict[str, Any]:
    cards = await sources_q.source_overview()
    events = await sources_q.events_per_day()
    return {
        "sources": [
            {
                "source": c.source,
                "last_run_at": c.last_run_at.isoformat() if c.last_run_at else None,
                "last_success_at": c.last_success_at.isoformat() if c.last_success_at else None,
                "stale": c.stale,
                "jobs": [
                    {
                        "id": j.id,
                        "status": j.status,
                        "started_at": j.started_at.isoformat() if j.started_at else None,
                        "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                        "duration_s": j.duration_s,
                        "emitted": j.emitted,
                        "written": j.written,
                        "error": j.error,
                    }
                    for j in c.jobs
                ],
            }
            for c in cards
        ],
        "events_per_day": [
            {"day": d.day, "samples": d.samples, "news": d.news, "games": d.games} for d in events
        ],
    }
