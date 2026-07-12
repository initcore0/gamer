"""Dashboard/genres/news-sources/CORS endpoint unit tests (API_CONTRACT.md).

Routes run through FastAPI with the query layer monkeypatched (no Postgres);
``next_digest_at`` is a pure helper unit-tested directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.dashboard as dashboard_route
import gamer.api.routes.games as games_route
import gamer.api.routes.news as news_route
from gamer.api import build_api
from gamer.api.queries.status import (
    DashboardPayload,
    DashboardRecommendation,
    DashboardTopMover,
    LastDigest,
    next_digest_at,
)
from gamer.config import Settings, get_settings

# ── /api/v1/dashboard ────────────────────────────────────────────────────────

_DASHBOARD = DashboardPayload(
    top_movers=[DashboardTopMover(game_id=1, name="Celeste", latest=1000.0, delta=50.0, pct=5.3)],
    latest_recommendations=[
        DashboardRecommendation(
            id=9,
            game_id=1,
            game_name="Celeste",
            score=0.61,
            user_key="default",
            created_at="2026-07-12T00:00:00+00:00",
        )
    ],
    last_digest=LastDigest(channel="telegram_group", sent_at="2026-07-12T16:00:00+00:00"),
    next_digest_at="2026-07-13T16:00:00+00:00",
)


def test_dashboard_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake() -> DashboardPayload:
        return _DASHBOARD

    monkeypatch.setattr(dashboard_route.status_q, "build_dashboard", _fake)
    client = TestClient(build_api())
    resp = client.get("/api/v1/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "top_movers",
        "latest_recommendations",
        "last_digest",
        "next_digest_at",
    }
    assert body["top_movers"][0] == {
        "game_id": 1,
        "name": "Celeste",
        "latest": 1000.0,
        "delta": 50.0,
        "pct": 5.3,
    }
    assert body["latest_recommendations"][0]["user_key"] == "default"
    assert body["last_digest"] == {
        "channel": "telegram_group",
        "sent_at": "2026-07-12T16:00:00+00:00",
    }
    assert body["next_digest_at"] == "2026-07-13T16:00:00+00:00"


# ── next_digest_at pure helper ───────────────────────────────────────────────


def test_next_digest_at_later_today() -> None:
    now = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)  # before 16:00
    fire = next_digest_at(16, group_chat_id=-100, now=now)
    assert fire == datetime(2026, 7, 12, 16, 0, tzinfo=UTC)


def test_next_digest_at_rolls_to_tomorrow() -> None:
    now = datetime(2026, 7, 12, 18, 0, tzinfo=UTC)  # after 16:00
    fire = next_digest_at(16, group_chat_id=-100, now=now)
    assert fire == datetime(2026, 7, 13, 16, 0, tzinfo=UTC)


def test_next_digest_at_exactly_on_the_hour_rolls_forward() -> None:
    now = datetime(2026, 7, 12, 16, 0, tzinfo=UTC)  # == fire time
    fire = next_digest_at(16, group_chat_id=-100, now=now)
    assert fire == datetime(2026, 7, 13, 16, 0, tzinfo=UTC)


def test_next_digest_at_none_without_group_chat() -> None:
    now = datetime(2026, 7, 12, 10, 0, tzinfo=UTC)
    assert next_digest_at(16, group_chat_id=0, now=now) is None


def test_next_digest_at_coerces_naive_now_to_utc() -> None:
    now = datetime(2026, 7, 12, 10, 0)  # naive → assumed UTC
    fire = next_digest_at(16, group_chat_id=-100, now=now)
    assert fire == datetime(2026, 7, 12, 16, 0, tzinfo=UTC)


# ── /api/v1/genres and /api/v1/news/sources ──────────────────────────────────


def test_genres_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake() -> list[str]:
        return ["Action", "Puzzle", "Roguelike"]

    monkeypatch.setattr(games_route.games_q, "list_genres", _fake)
    client = TestClient(build_api())
    resp = client.get("/api/v1/genres")
    assert resp.status_code == 200
    assert resp.json() == {"genres": ["Action", "Puzzle", "Roguelike"]}


def test_news_sources_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake() -> list[str]:
        return ["pcgamer", "rps"]

    monkeypatch.setattr(news_route.news_q, "news_sources", _fake)
    client = TestClient(build_api())
    resp = client.get("/api/v1/news/sources")
    assert resp.status_code == 200
    assert resp.json() == {"sources": ["pcgamer", "rps"]}


# ── CORS wiring ──────────────────────────────────────────────────────────────


def _build_with_cors(monkeypatch: pytest.MonkeyPatch, csv: str | None) -> TestClient:
    if csv is None:
        monkeypatch.delenv("GAMER_UI__CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("GAMER_UI__CORS_ORIGINS", csv)
    get_settings.cache_clear()
    return TestClient(build_api(Settings()))


def test_cors_header_present_when_origins_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_with_cors(monkeypatch, "http://localhost:5173,https://ui.example.com")
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_header_absent_when_no_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _build_with_cors(monkeypatch, None)
    resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in resp.headers
