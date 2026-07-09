"""Read-only status API for the public build log (PLAN.md §4.8 / §6 M4).

A tiny FastAPI app that reports what the system has ingested and whether it is
healthy — no writes, no auth, safe to expose as the build-in-public log:

* ``GET /health`` → liveness only, no DB touch.
* ``GET /status`` → per-source last_run/last_success, row counts, the most
  recent recommendations, and which sources are STALE.
* ``GET /`` → a tiny server-rendered HTML view of ``/status`` (no external
  assets).

Everything reads through :func:`session_scope`; the app is *never* auto-started
in tests. Use :func:`build_api` to get a bare app for ``TestClient``, and
:func:`run_api` to actually serve it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select

from gamer.config import Settings, get_settings
from gamer.db import session_scope
from gamer.db.models import (
    Game,
    NewsItem,
    Recommendation,
    SignalSample,
)
from gamer.health import find_stale_sources
from gamer.logging import get_logger
from gamer.sources.runner import latest_source_status

log = get_logger("api")

_RECENT_REC_LIMIT = 10


async def _counts() -> dict[str, int]:
    async with session_scope() as session:
        games = await session.scalar(select(func.count()).select_from(Game))
        news = await session.scalar(select(func.count()).select_from(NewsItem))
        signals = await session.scalar(select(func.count()).select_from(SignalSample))
        recs = await session.scalar(select(func.count()).select_from(Recommendation))
    return {
        "games": int(games or 0),
        "news": int(news or 0),
        "signals": int(signals or 0),
        "recommendations": int(recs or 0),
    }


async def _recent_recommendations() -> list[dict[str, Any]]:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Recommendation.score, Game.name, Recommendation.created_at)
                .join(Game, Game.id == Recommendation.game_id)
                .order_by(Recommendation.created_at.desc())
                .limit(_RECENT_REC_LIMIT)
            )
        ).all()
    return [
        {
            "name": name,
            "score": round(float(score), 4),
            "created_at": created_at.isoformat() if created_at else None,
        }
        for score, name, created_at in rows
    ]


async def build_status(*, now: datetime | None = None) -> dict[str, Any]:
    """Assemble the full ``/status`` payload from read-only DB queries."""
    now = now or datetime.now(UTC)
    sources = await latest_source_status()
    counts = await _counts()
    recent = await _recent_recommendations()
    stale = await find_stale_sources(now)
    return {
        "generated_at": now.isoformat(),
        "sources": sources,
        "stale_sources": stale,
        "counts": counts,
        "recent_recommendations": recent,
    }


def _render_index(status: dict[str, Any]) -> str:
    """Server-rendered HTML view of the status payload — no external assets."""
    counts = status["counts"]
    stale = set(status["stale_sources"])

    count_rows = "".join(
        f"<tr><td>{escape(label)}</td><td>{value:,}</td></tr>"
        for label, value in (
            ("Games", counts["games"]),
            ("News items", counts["news"]),
            ("Signal samples", counts["signals"]),
            ("Recommendations", counts["recommendations"]),
        )
    )

    def _source_row(s: dict[str, Any]) -> str:
        is_stale = s["source"] in stale
        cls = "stale" if is_stale else ""
        badge = "⚠️ STALE" if is_stale else "ok"
        return (
            f"<tr class='{cls}'><td>{escape(s['source'])}</td>"
            f"<td>{escape(s['last_run_at'] or '—')}</td>"
            f"<td>{escape(s['last_success_at'] or '—')}</td>"
            f"<td>{badge}</td></tr>"
        )

    source_rows = "".join(_source_row(s) for s in status["sources"]) or (
        "<tr><td colspan='4'>No sources have run yet.</td></tr>"
    )

    rec_rows = (
        "".join(
            f"<tr><td>{escape(r['name'])}</td><td>{r['score']}</td></tr>"
            for r in status["recent_recommendations"]
        )
        or "<tr><td colspan='2'>No recommendations yet.</td></tr>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>gamer — status</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 820px; margin: 2rem auto;
          padding: 0 1rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.05rem; margin-top: 1.8rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .4rem; }}
  th, td {{ text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #e2e2e2;
            font-variant-numeric: tabular-nums; }}
  tr.stale td {{ background: #fff4f4; }}
  .muted {{ color: #777; font-size: .85rem; }}
</style>
</head>
<body>
<h1>🎮 gamer — build log status</h1>
<p class="muted">Generated at {escape(status["generated_at"])}</p>
<h2>Counts</h2>
<table><tbody>{count_rows}</tbody></table>
<h2>Sources</h2>
<table>
<thead><tr><th>Source</th><th>Last run</th><th>Last success</th><th></th></tr></thead>
<tbody>{source_rows}</tbody>
</table>
<h2>Recent recommendations</h2>
<table>
<thead><tr><th>Game</th><th>Score</th></tr></thead>
<tbody>{rec_rows}</tbody>
</table>
</body>
</html>"""


def build_api(settings: Settings | None = None) -> FastAPI:
    """Construct the read-only status FastAPI app. Does not start a server."""
    settings = settings or get_settings()
    app = FastAPI(title="gamer status", version="0.1.0", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:  # DB-free liveness probe.
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return await build_status()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _render_index(await build_status())

    return app


async def run_api(settings: Settings | None = None) -> None:
    """Serve the status API via uvicorn on ``settings.health.{api_host,api_port}``."""
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
