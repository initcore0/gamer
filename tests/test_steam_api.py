from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from gamer.config import get_settings
from gamer.sources import REGISTRY
from gamer.sources.base import EventKind, FetchContext, RawEvent, Source
from gamer.sources.steam_api import SteamApiSource

_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
_STORE_APP_LIST_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
_PLAYER_COUNT_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"


def _source(appids: list[int], *, app_list_page_size: int = 1000) -> SteamApiSource:
    """Build a source with an injected (DB-free), low-retry appids provider."""
    return SteamApiSource(
        appids_provider=lambda: appids,
        rate=1000,
        per=1.0,
        max_attempts=2,
        app_list_page_size=app_list_page_size,
    )


def _set_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a Steam API key so the catalog sync takes the paginated path."""
    monkeypatch.setenv("GAMER_STEAM__API_KEY", "DEADBEEFDEADBEEFDEADBEEFDEADBEEF")
    get_settings.cache_clear()


def _store_page(apps: list[dict[str, object]], *, last_appid: int, have_more: bool) -> dict:
    """Shape a fake ``IStoreService/GetAppList/v1`` response page."""
    return {
        "response": {
            "apps": apps,
            "have_more_results": have_more,
            "last_appid": last_appid,
        }
    }


async def _collect(source: SteamApiSource, ctx: FetchContext) -> list[RawEvent]:
    return [e async for e in source.fetch(ctx)]


def test_source_satisfies_protocol() -> None:
    assert isinstance(SteamApiSource(appids_provider=lambda: []), Source)


def test_registered_in_registry() -> None:
    assert "steam_api" in REGISTRY
    assert REGISTRY["steam_api"]().name == "steam_api"


@respx.mock
async def test_app_list_emits_game_events() -> None:
    respx.get(_APP_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "applist": {
                    "apps": [
                        {"appid": 440, "name": "Team Fortress 2"},
                        {"appid": 10, "name": "Counter-Strike"},
                    ]
                }
            },
        )
    )
    source = _source([])  # no tracked games -> no player-count calls
    ctx = FetchContext()
    events = await _collect(source, ctx)

    game_events = [e for e in events if e.kind is EventKind.GAME]
    assert len(game_events) == 2
    # Deterministically ordered by appid; natural_key is the appid.
    first = game_events[0]
    assert first.natural_key == "10"
    assert first.platform_app_id == 10
    assert first.payload == {"name": "Counter-Strike"}
    assert first.occurred_at.tzinfo is not None
    assert first.dedup_key() == "steam_api:game:10"


@respx.mock
async def test_player_count_emits_player_count_events() -> None:
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 12345, "result": 1}})
    )
    source = _source([730])
    events = await _collect(source, FetchContext())

    pc = [e for e in events if e.kind is EventKind.PLAYER_COUNT]
    assert len(pc) == 1
    event = pc[0]
    assert event.payload == {"players": 12345}
    assert event.platform_app_id == 730
    # natural_key = "{appid}:{iso_hour}" — hour-truncated, tz-aware.
    appid_part, iso_hour = event.natural_key.split(":", 1)
    assert appid_part == "730"
    parsed = datetime.fromisoformat(iso_hour)
    assert parsed.tzinfo is not None
    assert (parsed.minute, parsed.second, parsed.microsecond) == (0, 0, 0)


@respx.mock
async def test_player_count_skips_apps_without_stats() -> None:
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    # result != 1 means the app has no player-count stats — skip it, don't emit.
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"result": 42}})
    )
    source = _source([999999])
    events = await _collect(source, FetchContext())
    assert [e for e in events if e.kind is EventKind.PLAYER_COUNT] == []


@respx.mock
async def test_limit_is_honoured_and_cursor_advances() -> None:
    apps = [{"appid": i, "name": f"game-{i}"} for i in (1, 2, 3, 4, 5)]
    respx.get(_APP_LIST_URL).mock(
        return_value=httpx.Response(200, json={"applist": {"apps": apps}})
    )
    source = _source([])
    ctx = FetchContext(limit=2)
    events = await _collect(source, ctx)

    assert len(events) == 2
    assert [e.natural_key for e in events] == ["1", "2"]
    # Cursor resumes where we stopped; not a full sync yet.
    assert ctx.cursor["app_list_index"] == 2
    assert "last_full_sync" not in ctx.cursor

    # A second run resumes from the checkpoint.
    events2 = await _collect(source, ctx)
    assert [e.natural_key for e in events2] == ["3", "4"]
    assert ctx.cursor["app_list_index"] == 4


@respx.mock
async def test_full_sync_marks_cursor() -> None:
    apps = [{"appid": 1, "name": "a"}, {"appid": 2, "name": "b"}]
    respx.get(_APP_LIST_URL).mock(
        return_value=httpx.Response(200, json={"applist": {"apps": apps}})
    )
    source = _source([])
    ctx = FetchContext()
    await _collect(source, ctx)
    assert ctx.cursor["app_list_index"] == 2
    assert "last_full_sync" in ctx.cursor


@respx.mock
async def test_app_list_429_is_handled_gracefully() -> None:
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(429))
    # A tracked game still lets us verify player-count runs after a failed app-list.
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 7, "result": 1}})
    )
    source = _source([440])
    # Must not raise despite the app-list returning 429 after retries.
    events = await _collect(source, FetchContext())
    assert [e.kind for e in events if e.kind is EventKind.GAME] == []
    assert [e.payload for e in events if e.kind is EventKind.PLAYER_COUNT] == [{"players": 7}]


@respx.mock
async def test_player_count_500_is_handled_gracefully() -> None:
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_PLAYER_COUNT_URL).mock(return_value=httpx.Response(500))
    source = _source([440])
    # 500 after retries must be swallowed — no exception escapes fetch.
    events = await _collect(source, FetchContext())
    assert events == []


# --- Key-authenticated, paginated catalog path (IStoreService/GetAppList/v1) -------


@respx.mock
async def test_paginated_app_list_emits_game_events(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    route = respx.get(_STORE_APP_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json=_store_page(
                [
                    {"appid": 10, "name": "Counter-Strike"},
                    {"appid": 440, "name": "Team Fortress 2"},
                ],
                last_appid=440,
                have_more=False,
            ),
        )
    )
    source = _source([])  # no tracked games -> no player-count calls
    events = await _collect(source, FetchContext())

    game_events = [e for e in events if e.kind is EventKind.GAME]
    assert [e.natural_key for e in game_events] == ["10", "440"]
    assert game_events[0].payload == {"name": "Counter-Strike"}
    assert game_events[0].platform_app_id == 10
    # The preferred key-authenticated endpoint was used, not the keyless dump.
    assert route.called
    request = route.calls.last.request
    assert "key=DEADBEEF" in str(request.url)
    assert "last_appid=0" in str(request.url)


@respx.mock
async def test_paginated_cursor_advances_across_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``last_appid`` cursor must resume past what a prior run already emitted."""
    _set_key(monkeypatch)

    def _by_cursor(request: httpx.Request) -> httpx.Response:
        last_appid = int(request.url.params.get("last_appid", "0"))
        # Full catalog of four apps; the API returns only those *after* the cursor.
        catalog = [
            {"appid": 10, "name": "a"},
            {"appid": 20, "name": "b"},
            {"appid": 30, "name": "c"},
            {"appid": 40, "name": "d"},
        ]
        remaining = [a for a in catalog if int(a["appid"]) > last_appid]
        page = remaining[:2]
        highest = int(page[-1]["appid"]) if page else last_appid
        have_more = len(remaining) > len(page)
        return httpx.Response(200, json=_store_page(page, last_appid=highest, have_more=have_more))

    respx.get(_STORE_APP_LIST_URL).mock(side_effect=_by_cursor)

    # page size 2 so each run emits exactly one page and stops.
    source = _source([], app_list_page_size=2)
    ctx = FetchContext()

    events1 = await _collect(source, ctx)
    assert [e.natural_key for e in events1 if e.kind is EventKind.GAME] == ["10", "20"]
    assert ctx.cursor["last_appid"] == 20
    assert "last_full_sync" not in ctx.cursor

    events2 = await _collect(source, ctx)
    assert [e.natural_key for e in events2 if e.kind is EventKind.GAME] == ["30", "40"]
    # Reached the end of the catalog: cursor resets and a full sync is recorded.
    assert ctx.cursor["last_appid"] == 0
    assert "last_full_sync" in ctx.cursor

    # A third run starts a fresh pass from the top.
    events3 = await _collect(source, ctx)
    assert [e.natural_key for e in events3 if e.kind is EventKind.GAME] == ["10", "20"]


@respx.mock
async def test_paginated_limit_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    respx.get(_STORE_APP_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json=_store_page(
                [{"appid": i, "name": f"g{i}"} for i in (10, 20, 30, 40, 50)],
                last_appid=50,
                have_more=True,
            ),
        )
    )
    source = _source([])
    ctx = FetchContext(limit=2)
    events = await _collect(source, ctx)

    assert [e.natural_key for e in events] == ["10", "20"]
    assert ctx.cursor["last_appid"] == 20
    # Budget spent mid-catalog — not a full sync.
    assert "last_full_sync" not in ctx.cursor


@respx.mock
async def test_paginated_app_list_degrades_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404/403 on the paginated endpoint must not crash the run."""
    _set_key(monkeypatch)
    respx.get(_STORE_APP_LIST_URL).mock(return_value=httpx.Response(404))
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 9, "result": 1}})
    )
    source = _source([730])
    events = await _collect(source, FetchContext())

    # No catalog rows, but the run continues and player counts still flow.
    assert [e for e in events if e.kind is EventKind.GAME] == []
    assert [e.payload for e in events if e.kind is EventKind.PLAYER_COUNT] == [{"players": 9}]


@respx.mock
async def test_no_key_uses_keyless_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a key, catalog sync falls back to the keyless v2 dump."""
    monkeypatch.delenv("GAMER_STEAM__API_KEY", raising=False)
    get_settings.cache_clear()

    keyless = respx.get(_APP_LIST_URL).mock(
        return_value=httpx.Response(200, json={"applist": {"apps": [{"appid": 5, "name": "x"}]}})
    )
    store = respx.get(_STORE_APP_LIST_URL).mock(return_value=httpx.Response(404))
    source = _source([])
    events = await _collect(source, FetchContext())

    assert [e.natural_key for e in events if e.kind is EventKind.GAME] == ["5"]
    assert keyless.called
    assert not store.called
