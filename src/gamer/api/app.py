"""JSON API + built React SPA for the public build log (PLAN.md §4.8; UI_PLAN.md).

A small FastAPI app — the read/write surface is the JSON API under ``/api/v1``;
everything else serves the built React single-page app:

* ``GET /health``     → liveness only, no DB touch.
* ``GET /status``     → legacy JSON status payload, kept backward-compatible.
* ``GET /api/v1/...`` → the JSON API the SPA consumes (routes in ``routes/``).
* everything else     → the React SPA shell (``routes.spa`` catch-all), so client
  routes resolve on a hard refresh / deep link.

Wiring only lives here: CORS, router includes, and the SPA mount (installed last,
since its catch-all is greedy). The data layer is ``queries/``; handlers are
``routes/``. Nothing is auto-started in tests — :func:`build_api` returns a bare
app; :func:`run_api` serves it. ``build_status`` is re-exported for compatibility.
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gamer.api.queries.status import StatusPayload, build_status
from gamer.api.routes import dashboard, game_detail, games, news, recs, sources, users
from gamer.api.spa import mount_spa, spa_dist_dir
from gamer.config import Settings, get_settings
from gamer.logging import get_logger

__all__ = ["build_api", "build_status", "run_api"]

log = get_logger("api")


def build_api(settings: Settings | None = None) -> FastAPI:
    """Construct the JSON API + SPA FastAPI app. Does not start a server."""
    settings = settings or get_settings()
    app = FastAPI(title="gamer", version="0.3.0")

    # CORS: only added when origins are configured (GAMER_UI__CORS_ORIGINS). An
    # empty list keeps the app same-origin — no middleware. The SPA is served from
    # the same origin as the API, so this only matters for a split dev/prod origin.
    if settings.ui.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.ui.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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

    # Serve the built SPA LAST — its catch-all is greedy and would shadow the API
    # routes above. When web/dist is absent (dev/CI) this only adds a JSON-404
    # fallback, so the API-only app still boots.
    mount_spa(app, spa_dist_dir(settings.ui.spa_dist))
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
