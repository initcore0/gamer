"""Serve the built React SPA from the FastAPI app (API_CONTRACT.md §CORS & serving).

The React build (``web/dist``) is a static bundle: a hashed ``assets/`` tree plus
an ``index.html`` entry point. This module wires it into the app so that:

* ``/assets/*`` (and any other real file under the dist dir) is served verbatim
  by :class:`~starlette.staticfiles.StaticFiles` with far-future caching on the
  hashed files;
* every *other* GET path falls through to ``index.html`` so the client-side
  router (react-router) can resolve ``/games/42``, ``/news`` etc. on a hard
  refresh or deep link;
* the API surface is never swallowed — the catch-all explicitly excludes paths
  under ``/api/``, ``/static/``, ``/status`` and the health path, returning a
  plain JSON 404 for anything reserved-but-unmatched.

Dev/CI have no build: when the dist dir is missing, :func:`mount_spa` installs
only the JSON-404 catch-all, so ``build_api()`` still boots and serves the API.
The dist directory is resolved from ``GAMER_UI__SPA_DIST`` (default ``web/dist``).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from gamer.logging import get_logger

__all__ = ["mount_spa", "spa_dist_dir"]

log = get_logger("api.spa")

# Reserved prefixes the SPA catch-all must never intercept. A path that starts
# with one of these but matched no route is a genuine 404 (JSON), not an SPA
# route. ``/health`` is app.py's DB-free liveness probe; ``/status`` is the
# legacy JSON status payload.
_RESERVED_PREFIXES: tuple[str, ...] = ("/api/", "/static/", "/status", "/health")


def spa_dist_dir(raw: str) -> Path:
    """Resolve the configured SPA dist path to an absolute :class:`Path`.

    Relative values (the ``web/dist`` default) resolve against the current
    working directory, which is the repo root in dev and ``/app`` in the Docker
    image (where the build copies ``web/dist`` and sets ``GAMER_UI__SPA_DIST``).
    """
    return Path(raw).expanduser().resolve()


def _is_reserved(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in _RESERVED_PREFIXES)


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Install SPA serving on ``app`` (assets mount + index.html catch-all).

    When ``dist`` is missing, install only a JSON-404 catch-all so the API-only
    app still boots. Call this *last*, after every real route is registered — the
    catch-all is a greedy ``/{full_path:path}`` and would shadow later routes.
    """
    index = dist / "index.html"

    if not dist.is_dir() or not index.is_file():
        log.info("spa_absent", dist=str(dist))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_missing(full_path: str) -> Response:
            # No build present: the API is fully functional, but there is no UI
            # to serve. Reserved 404s and every unknown path degrade to JSON.
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        return

    log.info("spa_present", dist=str(dist))

    # Mount the whole dist tree so hashed assets (``/assets/*``) and any other
    # real file (favicon, manifest) are served directly. ``html=False`` keeps
    # StaticFiles from serving index.html on directory hits — that is the
    # catch-all's job, so client routes and the root share one code path.
    app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="spa-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(request: Request, full_path: str) -> Response:
        path = request.url.path
        # Never intercept the API/ops surface: an unmatched reserved path is a
        # real 404, returned as JSON (not the SPA shell).
        if _is_reserved(path):
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # A real static file under the dist root (e.g. ``/favicon.ico``,
        # ``/robots.txt``) — serve it verbatim; otherwise hand the client router
        # its index.html shell.
        if full_path:
            candidate = (dist / full_path).resolve()
            # Guard against ``..`` traversal escaping the dist dir.
            if candidate.is_file() and dist in candidate.parents:
                return FileResponse(str(candidate))

        return FileResponse(str(index))
