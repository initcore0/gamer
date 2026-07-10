"""Game-detail route + pure-helper tests (UI_PLAN.md §3.3, UI-M3).

Queries are monkeypatched so routes run through FastAPI without Postgres. Covers:
the detail page render, the 404 page, the JSON twin, the series endpoint
(cache header + 422 on bad params), CSP header on HTML, and the pure breakdown /
news-grouping helpers. Live-DB queries are in ``test_api_game_detail_integration``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.game_detail as detail_route
from gamer.api import build_api
from gamer.api.queries.game_detail import (
    Breakdown,
    GameDetail,
    NewsCard,
    NewsRef,
    SimilarGame,
    group_news_by_cluster,
)
from gamer.api.queries.signals import Series
from gamer.api.routes.game_detail import _breakdown_bars, _breakdown_penalties, _steam_store_url

_NOW = datetime(2026, 7, 9, tzinfo=UTC)

_DETAIL = GameDetail(
    id=42,
    name="Celeste",
    platform="steam",
    platform_app_id=504230,
    genres=["Platformer"],
    release_date=_NOW,
    price_cents=1999,
    is_free=False,
    tracked=True,
    current_players=1234.0,
    players_24h_delta=56.0,
    review_count=9000.0,
    twitch_viewers=42.0,
    last_signal_at=_NOW,
)

_BREAKDOWN = Breakdown(
    score=0.83,
    breakdown={
        "momentum": {"weight": 0.4, "value": 0.9, "weighted": 0.36, "reason": "rising fast"},
        "hype": {"weight": 0.2, "value": 0.1, "weighted": -0.05, "reason": "cooling"},
        "penalty:cooldown": {"multiplier": 0.5, "reason": "recently streamed"},
    },
    created_at=_NOW,
)


def _patch(monkeypatch: pytest.MonkeyPatch, *, detail: GameDetail | None = _DETAIL) -> None:
    async def _game_detail(game_id: int) -> GameDetail | None:
        return detail

    async def _latest_breakdown(game_id: int) -> Breakdown | None:
        return _BREAKDOWN

    async def _game_news(game_id: int, *, limit: int = 20) -> list[NewsCard]:
        return [
            NewsCard(
                id=1,
                title="Big update",
                url="https://example.com/a",
                source="rss",
                published_at=_NOW,
                cluster_id=7,
                similar_count=1,
                similar=[NewsRef(2, "Same story", "https://example.com/b", "rss2", _NOW)],
            )
        ]

    async def _similar_games(game_id: int, *, limit: int = 6) -> list[SimilarGame]:
        return [SimilarGame(id=99, name="Hades", platform="steam", distance=0.1)]

    async def _series(game_id: int, metric: object, range_key: object, **kw: object) -> Series:
        return Series(ts=[1000, 2000], values=[1.0, 2.0])

    monkeypatch.setattr(detail_route.detail_q, "game_detail", _game_detail)
    monkeypatch.setattr(detail_route.detail_q, "latest_breakdown", _latest_breakdown)
    monkeypatch.setattr(detail_route.detail_q, "game_news", _game_news)
    monkeypatch.setattr(detail_route.detail_q, "similar_games", _similar_games)
    monkeypatch.setattr(detail_route.signals_q, "series", _series)


def test_detail_page_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/games/42")
    assert resp.status_code == 200
    html = resp.text
    assert "Celeste" in html
    assert "store.steampowered.com/app/504230" in html
    assert 'rel="noopener"' in html
    assert "momentum" in html  # score bar
    assert "recently streamed" in html  # penalty reason
    assert "Big update" in html  # news
    assert "+1 similar" in html
    assert "Hades" in html  # similar game chip
    # Charts wired via static file, not inline script.
    assert "/static/charts.js" in html
    assert "<script>" not in html


def test_detail_page_has_csp_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/games/42")
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp


def test_detail_404_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, detail=None)
    client = TestClient(build_api())
    resp = client.get("/games/999")
    assert resp.status_code == 404
    assert "Game not found" in resp.text


def test_detail_json_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games/42")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 42
    assert body["name"] == "Celeste"
    assert body["breakdown"]["score"] == 0.83
    # JSON responses must NOT carry the HTML CSP.
    assert "content-security-policy" not in {k.lower() for k in resp.headers}


def test_detail_json_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, detail=None)
    client = TestClient(build_api())
    assert client.get("/api/v1/games/999").status_code == 404


def test_series_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games/42/series", params={"metric": "players", "range": "7d"})
    assert resp.status_code == 200
    assert resp.json() == {"ts": [1000, 2000], "values": [1.0, 2.0]}
    assert resp.headers["cache-control"] == "public, max-age=300"


def test_series_bad_metric_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games/42/series", params={"metric": "bogus", "range": "7d"})
    assert resp.status_code == 422


def test_series_bad_range_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games/42/series", params={"metric": "players", "range": "99y"})
    assert resp.status_code == 422


def test_series_missing_game_404(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, detail=None)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games/999/series", params={"metric": "players"})
    assert resp.status_code == 404


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_steam_store_url_only_for_steam() -> None:
    assert _steam_store_url(_DETAIL) == "https://store.steampowered.com/app/504230"
    xbox = GameDetail(
        id=1,
        name="X",
        platform="xbox",
        platform_app_id=5,
        genres=[],
        release_date=None,
        price_cents=None,
        is_free=True,
        tracked=False,
    )
    assert _steam_store_url(xbox) is None


def test_breakdown_bars_scale_to_max_abs() -> None:
    bars = _breakdown_bars(_BREAKDOWN)
    assert [b["key"] for b in bars] == ["momentum", "hype"]  # penalty excluded
    momentum = next(b for b in bars if b["key"] == "momentum")
    assert momentum["width_pct"] == 100.0  # largest abs weighted
    assert momentum["positive"] is True
    hype = next(b for b in bars if b["key"] == "hype")
    assert hype["positive"] is False
    assert hype["width_pct"] == pytest.approx(0.05 / 0.36 * 100.0)


def test_breakdown_penalties() -> None:
    pens = _breakdown_penalties(_BREAKDOWN)
    assert pens == [{"key": "cooldown", "multiplier": 0.5, "reason": "recently streamed"}]


def test_breakdown_helpers_none() -> None:
    assert _breakdown_bars(None) == []
    assert _breakdown_penalties(None) == []


def test_group_news_by_cluster() -> None:
    rows = [
        NewsRef(1, "A", None, "s1", _NOW, cluster_id=7),
        NewsRef(2, "B", None, "s2", _NOW, cluster_id=7),  # same cluster → folded
        NewsRef(3, "C", None, "s3", _NOW, cluster_id=None),  # unclustered → own card
        NewsRef(4, "D", None, "s4", _NOW, cluster_id=9),
    ]
    cards = group_news_by_cluster(rows, limit=10)
    assert [c.id for c in cards] == [1, 3, 4]
    primary = cards[0]
    assert primary.similar_count == 1
    assert [s.id for s in primary.similar] == [2]


def test_group_news_respects_limit() -> None:
    rows = [NewsRef(i, f"N{i}", None, "s", _NOW, cluster_id=None) for i in range(5)]
    cards = group_news_by_cluster(rows, limit=2)
    assert len(cards) == 2
