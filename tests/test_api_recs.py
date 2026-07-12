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
        user_key="default",
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
            user_key="default",
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
            user_key="default",
            created_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
            sent_at=None,
            feedback={"up": 0, "down": 0, "played": 0},
            breakdown={},
        ),
    ],
    next_cursor="Y3Vyc29y",
)


def _patch(monkeypatch: pytest.MonkeyPatch, page: RecPage = _FIXTURE) -> None:
    async def _fake(
        cursor: str | None = None,
        limit: int = 20,
        *,
        user_key: str | None = None,
    ) -> RecPage:
        return page

    monkeypatch.setattr(recs_route.recs_q, "list_recommendations", _fake)


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


def _capture_list(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch ``list_recommendations`` to record the ``user_key`` it was called with."""
    seen: dict[str, object] = {}

    async def _fake(
        cursor: str | None = None,
        limit: int = 20,
        *,
        user_key: str | None = None,
    ) -> RecPage:
        seen["user_key"] = user_key
        seen["limit"] = limit
        return _FIXTURE

    monkeypatch.setattr(recs_route.recs_q, "list_recommendations", _fake)
    return seen


def test_recs_json_default_user_key_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_list(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/recommendations")
    assert resp.status_code == 200
    # No explicit user_key → defaults to the legacy "default" profile filter.
    assert seen["user_key"] == "default"


def test_recs_json_explicit_user_key(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_list(monkeypatch)
    client = TestClient(build_api())
    client.get("/api/v1/recommendations", params={"user_key": "123456"})
    assert seen["user_key"] == "123456"


def test_recs_json_all_disables_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_list(monkeypatch)
    client = TestClient(build_api())
    client.get("/api/v1/recommendations", params={"user_key": "all"})
    # "all" → no pref_key filter (every profile's rows).
    assert seen["user_key"] is None


# ── POST /api/v1/recommendations/refresh ─────────────────────────────────────


def _fresh_page() -> RecPage:
    return RecPage(
        rows=[
            RecRow(
                id=42,
                game_id=7,
                game_name="Tunic",
                score=0.83,
                user_key="default",
                created_at=datetime(2026, 7, 12, 9, 0, tzinfo=UTC),
                sent_at=None,
                feedback={"up": 0, "down": 0, "played": 0},
                breakdown={"momentum": {"weighted": 0.2, "reason": "rising"}},
            )
        ],
        next_cursor=None,
    )


def _patch_refresh(
    monkeypatch: pytest.MonkeyPatch,
    *,
    known: set[str] | None = None,
    picks: int = 1,
) -> dict[str, object]:
    """Patch profile_keys, recommend, and the readback for the refresh route."""
    seen: dict[str, object] = {}

    async def _fake_keys() -> set[str]:
        return {"default", "123456"} if known is None else known

    async def _fake_recommend(
        limit: int = 5,
        key: str = "default",
        *,
        subscribed_quota: int = 0,
        **kwargs: object,
    ) -> list[object]:
        seen["recommend_limit"] = limit
        seen["recommend_key"] = key
        seen["subscribed_quota"] = subscribed_quota
        return [object()] * picks

    async def _fake_list(
        cursor: str | None = None,
        limit: int = 20,
        *,
        user_key: str | None = None,
    ) -> RecPage:
        seen["readback_key"] = user_key
        seen["readback_limit"] = limit
        return _fresh_page()

    monkeypatch.setattr(recs_route.users_q, "profile_keys", _fake_keys)
    monkeypatch.setattr(recs_route, "recommend", _fake_recommend)
    monkeypatch.setattr(recs_route.recs_q, "list_recommendations", _fake_list)
    return seen


def test_refresh_runs_scorer_and_returns_fresh_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_refresh(monkeypatch)
    client = TestClient(build_api())
    resp = client.post("/api/v1/recommendations/refresh", json={"user_key": "default", "limit": 7})
    assert resp.status_code == 200
    body = resp.json()
    # Same row shape as the GET, but no next_cursor.
    assert set(body) == {"recommendations"}
    assert seen["recommend_key"] == "default"
    assert seen["recommend_limit"] == 7
    assert seen["subscribed_quota"] == 3
    row = body["recommendations"][0]
    assert row["id"] == 42
    assert row["game_name"] == "Tunic"
    assert row["feedback"] == {"up": 0, "down": 0, "played": 0}


def test_refresh_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_refresh(monkeypatch)
    client = TestClient(build_api())
    resp = client.post("/api/v1/recommendations/refresh", json={})
    assert resp.status_code == 200
    assert seen["recommend_key"] == "default"
    assert seen["recommend_limit"] == 10  # contract default


def test_refresh_unknown_user_key_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_refresh(monkeypatch, known={"default"})
    client = TestClient(build_api())
    resp = client.post("/api/v1/recommendations/refresh", json={"user_key": "nope"})
    assert resp.status_code == 422


def test_refresh_all_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_refresh(monkeypatch)
    client = TestClient(build_api())
    resp = client.post("/api/v1/recommendations/refresh", json={"user_key": "all"})
    assert resp.status_code == 422


def test_refresh_empty_user_key_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_refresh(monkeypatch)
    client = TestClient(build_api())
    # Empty string fails the min_length=1 body validation → 422.
    resp = client.post("/api/v1/recommendations/refresh", json={"user_key": ""})
    assert resp.status_code == 422


def test_refresh_limit_out_of_range_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_refresh(monkeypatch)
    client = TestClient(build_api())
    assert (
        client.post(
            "/api/v1/recommendations/refresh", json={"user_key": "default", "limit": 21}
        ).status_code
        == 422
    )


def test_refresh_no_candidates_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_refresh(monkeypatch, picks=0)
    client = TestClient(build_api())
    resp = client.post("/api/v1/recommendations/refresh", json={"user_key": "default"})
    # Known profile with nothing to recommend → empty list, not an error.
    assert resp.status_code == 200
    assert resp.json() == {"recommendations": []}
