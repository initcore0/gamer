"""Status API tests.

``/health`` is liveness only and must work with no database — its test is a
plain unit test. ``/status`` and ``/`` touch the DB via helper coroutines; we
monkeypatch those helpers so the endpoints can be exercised through FastAPI's
``TestClient`` without a live Postgres. A DB-backed ``/status`` smoke test is
marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import gamer.api.app as api_app
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

    monkeypatch.setattr(api_app, "build_status", _fake_build_status)


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


def test_index_renders_html(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_status(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text
    assert "gamer" in html
    assert "Hades" in html
    assert "STALE" in html  # the stale rss source is badged


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
