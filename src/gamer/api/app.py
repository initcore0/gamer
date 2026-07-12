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

from typing import Any

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from gamer.api.queries.status import StatusPayload, build_status
from gamer.api.routes import dashboard, game_detail, games, news, recs, sources, users
from gamer.api.templating import STATIC_DIR
from gamer.config import Settings, get_settings
from gamer.logging import get_logger

__all__ = ["build_api", "build_status", "run_api"]

log = get_logger("api")

# CSP for HTML pages (UI_PLAN.md §2, §9): self-only, no external assets, no inline
# scripts (charts run from /static/charts.js reading data-* attrs). ``data:`` is
# allowed for images so inline sparkline/data-URI assets keep working.
_CSP = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'"


def build_api(settings: Settings | None = None) -> FastAPI:
    """Construct the read-only web-UI FastAPI app. Does not start a server."""
    settings = settings or get_settings()
    app = FastAPI(title="gamer", version="0.2.0", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # CORS: only added when origins are configured (GAMER_UI__CORS_ORIGINS). An
    # empty list keeps the app same-origin — no middleware, prod byte-identical.
    if settings.ui.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.ui.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def _csp_header(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        # Only HTML documents need the CSP; JSON/static responses are unaffected.
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            response.headers["Content-Security-Policy"] = _CSP
        return response

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
    app.include_router(game_detail.router)
    app.include_router(recs.router)
    app.include_router(news.router)
    app.include_router(sources.router)
    app.include_router(users.router)
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
