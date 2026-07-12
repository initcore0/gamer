"""Preference-profile endpoint unit tests (API_CONTRACT.md — user switcher).

The route is exercised with a monkeypatched query so it runs through FastAPI
without Postgres. The DB-backed shape check lives in the integration twin.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.users as users_route
from gamer.api import build_api
from gamer.api.queries.users import UserRow, _label_for

_FIXTURE = [
    UserRow(
        key="default",
        label="Legacy profile",
        liked_genres=[],
        blocked_genres=[],
        subscribed_genres=["Puzzle"],
        muted_count=1,
        digest_enabled=True,
        created_at=datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
    ),
    UserRow(
        key="123456",
        label="Ada",
        liked_genres=["Roguelike"],
        blocked_genres=["Sports"],
        subscribed_genres=[],
        muted_count=0,
        digest_enabled=False,
        created_at=datetime(2026, 7, 2, 0, 0, tzinfo=UTC),
    ),
]


def _patch(monkeypatch: pytest.MonkeyPatch, rows: list[UserRow] = _FIXTURE) -> None:
    async def _fake() -> list[UserRow]:
        return rows

    monkeypatch.setattr(users_route.users_q, "list_users", _fake)


def test_users_json_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/users")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"users"}
    first = body["users"][0]
    assert set(first) == {
        "key",
        "label",
        "liked_genres",
        "blocked_genres",
        "subscribed_genres",
        "muted_count",
        "digest_enabled",
        "created_at",
    }
    assert first["key"] == "default"
    assert first["label"] == "Legacy profile"
    assert first["subscribed_genres"] == ["Puzzle"]
    assert first["muted_count"] == 1
    assert first["created_at"] == "2026-07-01T00:00:00+00:00"
    assert body["users"][1]["label"] == "Ada"


def test_users_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, [])
    client = TestClient(build_api())
    assert client.get("/api/v1/users").json() == {"users": []}


def test_label_for_default_maps_null() -> None:
    # Pure mapping helper: the legacy default's NULL label surfaces as a name;
    # other profiles fall back to their key only if unlabeled (bot sets a label).
    assert _label_for("default", None) == "Legacy profile"
    assert _label_for("default", "Explicit") == "Explicit"
    assert _label_for("123", "Ada") == "Ada"
    assert _label_for("123", None) == "123"
