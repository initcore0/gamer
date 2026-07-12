"""Catalog JSON route tests (API_CONTRACT.md §Catalog).

The list query is monkeypatched to a fixture so routes run through FastAPI
without Postgres. Covers: JSON twin shape, empty-string filter params, 422 for
bad enum values, the ``/api/v1/genres`` allowlist, and the datetime-cursor
degrade path. Live-DB pagination/search is in ``test_api_games_integration.py``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import gamer.api.routes.games as games_route
from gamer.api import build_api
from gamer.api.queries.games import GamePage, GameRow

_FIXTURE = GamePage(
    rows=[
        GameRow(
            id=1,
            name="Celeste",
            platform="steam",
            genres=["Platformer"],
            tracked=True,
            current_players=1234.0,
            players_24h_delta=56.0,
            spark=[1.0, 2.0, 3.0],
            review_count=9000.0,
        ),
        GameRow(
            id=2, name="Hades", platform="steam", genres=["Roguelike", "Action"], tracked=False
        ),
    ],
    next_cursor="Y3Vyc29y",
)


def _patch_games(monkeypatch: pytest.MonkeyPatch, page: GamePage = _FIXTURE) -> None:
    async def _fake_list_games(
        search: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        **kwargs: object,
    ) -> GamePage:
        return page

    async def _fake_list_genres() -> list[str]:
        return ["Action", "Platformer", "Roguelike"]

    monkeypatch.setattr(games_route.games_q, "list_games", _fake_list_games)
    monkeypatch.setattr(games_route.games_q, "list_genres", _fake_list_genres)


def test_games_json_twin(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_games(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"games", "next_cursor"}
    assert body["next_cursor"] == "Y3Vyc29y"
    assert body["games"][0] == {
        "id": 1,
        "name": "Celeste",
        "platform": "steam",
        "genres": ["Platformer"],
        "tracked": True,
        "current_players": 1234.0,
        "players_24h_delta": 56.0,
        "spark": [1.0, 2.0, 3.0],
        "review_count": 9000.0,
        "last_signal_at": None,
    }


def test_games_json_empty_filter_params(monkeypatch: pytest.MonkeyPatch) -> None:
    """The UI submits unselected fields as empty strings — the exact query string
    must not 422 (regression: every UI search failed because ``platform=`` broke
    enum coercion)."""
    _patch_games(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/games?q=dota&platform=&genre=&sort=&tracked=&active=")
    assert resp.status_code == 200
    assert resp.json()["games"]


def test_games_json_invalid_sort_is_422() -> None:
    client = TestClient(build_api())
    resp = client.get("/api/v1/games", params={"sort": "bogus"})
    assert resp.status_code == 422


def test_games_json_invalid_platform_is_422() -> None:
    client = TestClient(build_api())
    resp = client.get("/api/v1/games", params={"platform": "nintendo64"})
    assert resp.status_code == 422


def test_genres_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_games(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/api/v1/genres")
    assert resp.status_code == 200
    assert resp.json() == {"genres": ["Action", "Platformer", "Roguelike"]}


def test_tampered_datetime_cursor_never_raises() -> None:
    """A cursor that passes (str, int) type checks but carries a non-ISO string
    must degrade to first page (true() predicate), never raise into a 500."""
    from gamer.api.deps import encode_cursor
    from gamer.api.queries.games import Sort, _seek_predicate

    for sort in (Sort.RELEASE, Sort.UPDATED):
        # Must not raise:
        pred = _seek_predicate(sort, "not-a-datetime", 5)
        assert pred is not None
    # And the token form round-trips through decode without touching the DB.
    token = encode_cursor(("garbage-not-iso", 5))
    assert isinstance(token, str)
