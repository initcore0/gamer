"""Dashboard / status JSON routes (API_CONTRACT.md §Ops).

* ``GET /api/v1/status``    → the frozen status payload.
* ``GET /api/v1/dashboard`` → everything the dashboard renders beyond ``/status``.

No SQL here — all reads go through :mod:`queries.status`. The React SPA renders
these; there is no server-side HTML page any more.
"""

from __future__ import annotations

from fastapi import APIRouter

from gamer.api.queries import status as status_q

router = APIRouter()


@router.get("/api/v1/status")
async def status_json() -> status_q.StatusPayload:
    return await status_q.build_status()


@router.get(
    "/api/v1/dashboard",
    tags=["ops"],
    summary="Dashboard payload (top movers, latest recs, digest state)",
)
async def dashboard_json() -> status_q.DashboardPayload:
    """Everything the dashboard renders beyond ``/status`` (API_CONTRACT.md)."""
    return await status_q.build_dashboard()
