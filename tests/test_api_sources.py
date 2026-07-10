"""Sources ops-view unit tests (UI_PLAN.md §3.6, UI-M4).

``_truncate_error`` (server-side error truncation, §7) is pure — unit-tested
directly. Routes are exercised with monkeypatched queries so they run through
FastAPI without Postgres. Live-DB overview/events are in the integration file.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.sources as sources_route
from gamer.api import build_api
from gamer.api.queries.sources import DayCounts, JobRow, SourceCard, _truncate_error


def test_truncate_error_none() -> None:
    assert _truncate_error(None) is None


def test_truncate_error_short_unchanged() -> None:
    assert _truncate_error("boom") == "boom"


def test_truncate_error_long_capped() -> None:
    out = _truncate_error("x" * 500)
    assert out is not None
    assert len(out) == 201  # 200 chars + the ellipsis
    assert out.endswith("…")


_CARD = SourceCard(
    source="rss",
    last_run_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
    last_success_at=None,
    stale=True,
    jobs=[
        JobRow(
            id=1,
            status="error",
            started_at=datetime(2026, 7, 9, 11, 0, tzinfo=UTC),
            finished_at=datetime(2026, 7, 9, 11, 0, 5, tzinfo=UTC),
            duration_s=5.0,
            emitted=10,
            written=7,
            error="HTTPError: boom",
        )
    ],
)
_EVENTS = [DayCounts(day="2026-07-08", samples=100, news=5, games=2)]


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_overview() -> list[SourceCard]:
        return [_CARD]

    async def _fake_events(days: int = 14) -> list[DayCounts]:
        return _EVENTS

    monkeypatch.setattr(sources_route.sources_q, "source_overview", _fake_overview)
    monkeypatch.setattr(sources_route.sources_q, "events_per_day", _fake_events)


def test_sources_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/sources")
    assert resp.status_code == 200
    html = resp.text
    assert "<html" in html
    assert "rss" in html
    assert "STALE" in html
    assert "HTTPError: boom" in html  # (truncated) error surfaced
    assert "<svg" in html  # events bar chart rendered server-side
    assert "Events per day" in html


def test_sources_json_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"sources", "events_per_day"}
    src = body["sources"][0]
    assert src["source"] == "rss"
    assert src["stale"] is True
    assert src["jobs"][0]["duration_s"] == 5.0
    assert body["events_per_day"][0] == {
        "day": "2026-07-08",
        "samples": 100,
        "news": 5,
        "games": 2,
    }
