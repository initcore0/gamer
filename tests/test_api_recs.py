"""Recommendation-feed unit tests (UI_PLAN.md §3.4, UI-M4).

``group_runs`` is pure (DB-free) — grouping by minute is the unit-test surface.
The route is exercised with a monkeypatched query so it runs through FastAPI
without Postgres. Live-DB keyset walk is in ``test_api_recs_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.recs as recs_route
from gamer.api import build_api
from gamer.api.queries.recs import RecPage, RecRow, group_runs


def _row(rec_id: int, minute: int, second: int = 0) -> RecRow:
    return RecRow(
        id=rec_id,
        game_id=rec_id,
        game_name=f"Game {rec_id}",
        score=0.5,
        created_at=datetime(2026, 7, 9, 12, minute, second, tzinfo=UTC),
        sent_at=None,
        feedback={"up": 0, "down": 0, "played": 0},
        breakdown={},
    )


def test_group_runs_empty() -> None:
    assert group_runs([]) == []


def test_group_runs_groups_by_minute() -> None:
    rows = [_row(1, 5, 1), _row(2, 5, 9), _row(3, 4, 0), _row(4, 4, 30)]
    groups = group_runs(rows)
    assert [len(g.rows) for g in groups] == [2, 2]
    assert groups[0].minute == datetime(2026, 7, 9, 12, 5, tzinfo=UTC)
    assert groups[1].minute == datetime(2026, 7, 9, 12, 4, tzinfo=UTC)


def test_group_runs_preserves_order_and_seconds_ignored() -> None:
    rows = [_row(1, 5, 59), _row(2, 5, 0), _row(3, 6, 0)]
    groups = group_runs(rows)
    # First two share minute 5 despite second differences; the third is its own.
    assert [[r.id for r in g.rows] for g in groups] == [[1, 2], [3]]


_FIXTURE = RecPage(
    rows=[
        RecRow(
            id=10,
            game_id=1,
            game_name="Hades",
            score=0.91,
            created_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
            sent_at=datetime(2026, 7, 9, 12, 1, tzinfo=UTC),
            feedback={"up": 2, "down": 0, "played": 1},
            breakdown={"momentum": {"weighted": 0.3, "reason": "surging"}},
        ),
        RecRow(
            id=11,
            game_id=2,
            game_name="Celeste",
            score=0.7,
            created_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
            sent_at=None,
            feedback={"up": 0, "down": 0, "played": 0},
            breakdown={},
        ),
    ],
    next_cursor="Y3Vyc29y",
)


def _patch(monkeypatch: pytest.MonkeyPatch, page: RecPage = _FIXTURE) -> None:
    async def _fake(cursor: str | None = None, limit: int = 20) -> RecPage:
        return page

    monkeypatch.setattr(recs_route.recs_q, "list_recommendations", _fake)


def test_recs_full_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/recommendations")
    assert resp.status_code == 200
    html = resp.text
    assert "<html" in html
    assert "Hades" in html
    assert "surging" in html  # breakdown bar reason rendered in the expander
    assert "sent" in html and "unsent" in html
    assert "Load more" in html


def test_recs_hx_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/recommendations", headers={"HX-Request": "true"})
    assert "<html" not in resp.text
    assert "Hades" in resp.text


def test_recs_json_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/recommendations")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"recommendations", "next_cursor"}
    assert body["next_cursor"] == "Y3Vyc29y"
    first = body["recommendations"][0]
    assert first["id"] == 10
    assert first["game_id"] == 1
    assert first["feedback"] == {"up": 2, "down": 0, "played": 1}
