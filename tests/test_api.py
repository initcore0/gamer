"""Status/dashboard API tests (UI_PLAN.md §8 UI-M1).

``/health`` is liveness only and must work with no database — a plain unit test.
The dashboard / ``/status`` / ``/api/v1/status`` endpoints touch the DB via
``queries.status.build_status``; we monkeypatch it so the routes run through
FastAPI without a live Postgres. A DB-backed smoke test is ``@integration``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import gamer.api.app as api_app
import gamer.api.routes.dashboard as dashboard_route
from gamer.api import build_api

_FAKE_STATUS: dict[str, Any] = {
    "generated_at": "2026-07-09T12:00:00+00:00",
    "sources": [
        {
            "source": "steam_api",
            "last_run_at": "2026-07-09T11:55:00+00:00",
            "last_success_at": "2026-07-09T11:55:00+00:00",
        },
        {
            "source": "rss",
            "last_run_at": "2026-07-08T00:00:00+00:00",
            "last_success_at": None,
        },
    ],
    "stale_sources": ["rss"],
    "counts": {"games": 3, "news": 7, "signals": 42, "recommendations": 2},
    "recent_recommendations": [
        {"name": "Hades", "score": 0.91, "created_at": "2026-07-09T10:00:00+00:00"},
        {"name": "Hollow Knight", "score": 0.84, "created_at": "2026-07-09T09:00:00+00:00"},
    ],
}


def test_health_is_db_free() -> None:
    """/health returns ok without any DB helpers being invoked."""
    client = TestClient(build_api())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def _patch_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_build_status(*, now: Any = None) -> dict[str, Any]:
        return _FAKE_STATUS

    # app.py's legacy /status and the dashboard route each hold their own
    # reference to build_status — patch both binding sites.
    async def _fake_top_movers() -> list[dict[str, Any]]:
        return [{"game_id": 1, "name": "Hades", "delta": 500.0, "spark": [1.0, 2.0, 3.0]}]

    async def _fake_latest_recs() -> list[dict[str, Any]]:
        return [{"game_id": 1, "name": "Hades", "score": 0.91}]

    async def _fake_last_digest() -> dict[str, Any] | None:
        return {"channel": "telegram_group", "sent_at": "2026-07-09T10:00:00+00:00"}

    monkeypatch.setattr(api_app, "build_status", _fake_build_status)
    monkeypatch.setattr(dashboard_route.status_q, "build_status", _fake_build_status)
    monkeypatch.setattr(dashboard_route.status_q, "top_movers", _fake_top_movers)
    monkeypatch.setattr(dashboard_route.status_q, "latest_recommendations", _fake_latest_recs)
    monkeypatch.setattr(dashboard_route.status_q, "last_digest", _fake_last_digest)


def test_status_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_status(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "generated_at",
        "sources",
        "stale_sources",
        "counts",
        "recent_recommendations",
    }
    assert body["stale_sources"] == ["rss"]
    assert body["counts"]["games"] == 3
    assert body["recent_recommendations"][0] == {
        "name": "Hades",
        "score": 0.91,
        "created_at": "2026-07-09T10:00:00+00:00",
    }


def test_status_json_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    """/api/v1/status returns the same shape as legacy /status."""
    _patch_status(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert set(resp.json()) == {
        "generated_at",
        "sources",
        "stale_sources",
        "counts",
        "recent_recommendations",
    }


def test_dashboard_renders_ported_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_status(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "gamer" in html
    assert "Counts" in html
    assert "Sources" in html
    assert "Recent recommendations" in html
    assert "Hades" in html
    assert "STALE" in html  # the stale rss source is badged
    # No external asset URLs — everything self-hosted under /static (§2, §9).
    assert "unpkg.com" not in html
    assert "/static/app.css" in html


@pytest.mark.integration
async def test_status_against_db() -> None:
    """DB-backed /status smoke test. Requires a live database via GAMER_DB__*."""
    payload = await api_app.build_status()
    assert set(payload) == {
        "generated_at",
        "sources",
        "stale_sources",
        "counts",
        "recent_recommendations",
    }
    assert isinstance(payload["counts"]["games"], int)
