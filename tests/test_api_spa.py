"""SPA-serving tests (API_CONTRACT.md §CORS & serving; spa.py).

Two worlds:

* dist **present** — a temp dir with ``index.html`` + ``assets/`` is wired via
  ``GAMER_UI__SPA_DIST``; ``/`` and a client route (``/games/42``) both serve the
  index shell, hashed assets serve verbatim, and ``/api/v1/...`` is never
  swallowed by the catch-all.
* dist **absent** — the app still boots, the API still answers, and unknown
  non-API paths return a plain JSON 404 (not an HTML shell).

The DB-touching status query is monkeypatched so ``/api/v1/status`` runs without
Postgres.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.dashboard as dashboard_route
from gamer.api import build_api
from gamer.config import Settings

_INDEX_HTML = "<!doctype html><html><body><div id=root></div></body></html>"
_ASSET_JS = "console.log('spa bundle');"


def _patch_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_build_status(*, now: Any = None) -> dict[str, Any]:
        return {
            "generated_at": "2026-07-09T12:00:00+00:00",
            "sources": [],
            "stale_sources": [],
            "counts": {"games": 0, "news": 0, "signals": 0, "recommendations": 0},
            "recent_recommendations": [],
        }

    monkeypatch.setattr(dashboard_route.status_q, "build_status", _fake_build_status)


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_INDEX_HTML)
    (dist / "assets" / "app-abc123.js").write_text(_ASSET_JS)
    return dist


def _client_with_dist(monkeypatch: pytest.MonkeyPatch, dist: Path) -> TestClient:
    _patch_status(monkeypatch)
    settings = Settings()
    settings.ui.spa_dist = str(dist)
    return TestClient(build_api(settings))


def test_spa_present_serves_index_at_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client_with_dist(monkeypatch, _dist(tmp_path))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<div id=root>" in resp.text


def test_spa_present_serves_index_for_client_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client_with_dist(monkeypatch, _dist(tmp_path))
    resp = client.get("/games/42")
    assert resp.status_code == 200
    assert "<div id=root>" in resp.text  # the SPA shell, resolved client-side


def test_spa_present_serves_hashed_asset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client_with_dist(monkeypatch, _dist(tmp_path))
    resp = client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "spa bundle" in resp.text


def test_spa_present_does_not_swallow_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client_with_dist(monkeypatch, _dist(tmp_path))
    # A real API route still returns JSON, not the SPA shell.
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    assert resp.json()["counts"]["games"] == 0
    # An unknown /api path is a JSON 404, never the index shell.
    missing = client.get("/api/v1/does-not-exist")
    assert missing.status_code == 404
    assert "<div id=root>" not in missing.text


def test_spa_absent_api_still_works(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_status(monkeypatch)
    settings = Settings()
    settings.ui.spa_dist = str(tmp_path / "nonexistent")
    client = TestClient(build_api(settings))
    # Health + API answer normally with no build present.
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/status").status_code == 200


def test_spa_absent_catch_all_is_json_404(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_status(monkeypatch)
    settings = Settings()
    settings.ui.spa_dist = str(tmp_path / "nonexistent")
    client = TestClient(build_api(settings))
    resp = client.get("/games/42")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
