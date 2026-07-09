from __future__ import annotations

from datetime import datetime

import httpx
import respx

from gamer.sources import REGISTRY
from gamer.sources.base import EventKind, FetchContext, RawEvent, Source
from gamer.sources.steam_api import SteamApiSource

_APP_LIST_URL = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
_PLAYER_COUNT_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"


def _source(appids: list[int]) -> SteamApiSource:
    """Build a source with an injected (DB-free), low-retry appids provider."""
    return SteamApiSource(
        appids_provider=lambda: appids,
        rate=1000,
        per=1.0,
        max_attempts=2,
    )


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
