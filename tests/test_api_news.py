"""News-stream route unit tests (UI_PLAN.md §3.5, UI-M4).

Queries are monkeypatched so routes run through FastAPI without Postgres. Covers
full page vs HX-Request fragment, the source-filter allowlist validation, and the
JSON twin shape. Live-DB grouping/pagination is in ``test_api_news_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.news as news_route
from gamer.api import build_api
from gamer.api.queries.game_detail import NewsCard
from gamer.api.queries.news import NewsPage

_CARD = NewsCard(
    id=1,
    title="Big Patch Lands",
    url="https://example.com/patch",
    source="pcgamer",
    published_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    cluster_id=5,
    similar_count=1,
    similar=[],
)
_PAGE = NewsPage(cards=[_CARD], next_cursor="Y3Vyc29y")


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_stream(
        source: str | None = None,
        game_id: int | None = None,
        cursor: str | None = None,
        limit: int = 30,
    ) -> NewsPage:
        captured["source"] = source
        captured["game_id"] = game_id
        return _PAGE

    async def _fake_sources() -> list[str]:
        return ["pcgamer", "rps"]

    monkeypatch.setattr(news_route.news_q, "news_stream", _fake_stream)
    monkeypatch.setattr(news_route.news_q, "news_sources", _fake_sources)
    news_route._CAPTURED = captured  # type: ignore[attr-defined]


def test_news_full_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/news")
    assert resp.status_code == 200
    html = resp.text
    assert "<html" in html
    assert "Big Patch Lands" in html
    assert 'rel="noopener"' in html
    assert "Load more" in html
    # Source select seeded from the allowlist.
    assert "pcgamer" in html and "rps" in html


def test_news_hx_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/news", headers={"HX-Request": "true"})
    assert "<html" not in resp.text
    assert "Big Patch Lands" in resp.text


def test_news_bad_source_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/news", params={"source": "not-a-source"})
    assert resp.status_code == 200
    # A source outside the allowlist degrades to None (unfiltered), never SQL.
    assert news_route._CAPTURED["source"] is None  # type: ignore[attr-defined]


def test_news_empty_filter_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter form submits unselected fields as empty strings — ``game_id=``
    must degrade to None (unfiltered), not 422 on int coercion."""
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/news?source=&game_id=")
    assert resp.status_code == 200
    assert news_route._CAPTURED["game_id"] is None  # type: ignore[attr-defined]


def test_news_valid_source_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    client.get("/news", params={"source": "pcgamer"})
    assert news_route._CAPTURED["source"] == "pcgamer"  # type: ignore[attr-defined]


def test_news_json_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/news")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"news", "next_cursor"}
    assert body["next_cursor"] == "Y3Vyc29y"
    assert body["news"][0]["title"] == "Big Patch Lands"
    assert body["news"][0]["similar_count"] == 1
