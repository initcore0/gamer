"""Read-only web UI + status API for the public build log (PLAN.md §4.8; UI_PLAN.md).

A small FastAPI app — no writes, no auth, safe to expose as the build-in-public
window into the system. UI-M1 grows the old single status page into a
server-rendered UI (Jinja2 + HTMX + vendored static assets):

* ``GET /health``       → liveness only, no DB touch.
* ``GET /``             → dashboard (ported status page) via ``routes.dashboard``.
* ``GET /games``        → paginated catalog list via ``routes.games``.
* ``GET /api/v1/...``   → JSON twins of the pages (same query functions).
* ``GET /status``       → legacy JSON status payload, kept backward-compatible.

Wiring only lives here (UI_PLAN.md §4): templates, the ``/static`` mount, and
router includes. The data layer is ``queries/``; handlers are ``routes/``.
Nothing is auto-started in tests — :func:`build_api` returns a bare app;
:func:`run_api` serves it. ``build_status`` is re-exported for compatibility.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from gamer.api.queries.status import StatusPayload, build_status
from gamer.api.routes import dashboard, games
from gamer.api.templating import STATIC_DIR
from gamer.config import Settings, get_settings
from gamer.logging import get_logger

__all__ = ["build_api", "build_status", "run_api"]

log = get_logger("api")


def build_api(settings: Settings | None = None) -> FastAPI:
    """Construct the read-only web-UI FastAPI app. Does not start a server."""
    settings = settings or get_settings()
    app = FastAPI(title="gamer", version="0.2.0", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:  # DB-free liveness probe.
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> StatusPayload:
        # Legacy JSON endpoint — same top-level keys as before UI-M1. The
        # /api/v1/status twin lives in routes.dashboard.
        return await build_status()

    app.include_router(dashboard.router)
    app.include_router(games.router)
    return app


async def run_api(settings: Settings | None = None) -> None:
    """Serve the web UI via uvicorn on ``settings.health.{api_host,api_port}``."""
    settings = settings or get_settings()
    config = uvicorn.Config(
        build_api(settings),
        host=settings.health.api_host,
        port=settings.health.api_port,
        log_level=settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    log.info("api_serving", host=settings.health.api_host, port=settings.health.api_port)
    await server.serve()
