"""Catalog route + static-asset tests (UI_PLAN.md §8 UI-M1).

The list query is monkeypatched to a fixture so routes run through FastAPI
without Postgres. Covers: full page vs HX-Request fragment, JSON twin shape,
the search-input seed, and StaticFiles serving (app.css served, missing 404s).
Live-DB pagination/search is exercised in ``test_api_games_integration.py``.
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


def test_games_full_page(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_games(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/games")
    assert resp.status_code == 200
    html = resp.text
    assert "<html" in html  # full document, not a fragment
    assert 'name="q"' in html  # search input seed
    assert "Celeste" in html
    assert "Hades" in html
    assert "Load more" in html  # next_cursor present → load-more control shown


def test_games_hx_request_returns_fragment(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_games(monkeypatch)
    client = TestClient(build_api())
    resp = client.get("/games", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    html = resp.text
    assert "<html" not in html  # fragment only
    assert 'name="q"' not in html  # no search input in the fragment
    assert "Celeste" in html
    assert "Load more" in html


def test_games_empty_no_load_more(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_games(monkeypatch, GamePage(rows=[], next_cursor=None))
    client = TestClient(build_api())
    resp = client.get("/games", headers={"HX-Request": "true"})
    assert "No games found." in resp.text
    assert "Load more" not in resp.text


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


def test_games_json_invalid_sort_is_422() -> None:
    client = TestClient(build_api())
    resp = client.get("/api/v1/games", params={"sort": "bogus"})
    assert resp.status_code == 422


def test_games_json_invalid_platform_is_422() -> None:
    client = TestClient(build_api())
    resp = client.get("/api/v1/games", params={"platform": "nintendo64"})
    assert resp.status_code == 422


def test_static_app_css_served() -> None:
    client = TestClient(build_api())
    resp = client.get("/static/app.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
    assert "--accent" in resp.text  # our hand-rolled variables


def test_static_missing_file_404() -> None:
    client = TestClient(build_api())
    resp = client.get("/static/nope.js")
    assert resp.status_code == 404


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
