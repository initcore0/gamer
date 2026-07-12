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
_MOST_PLAYED_URL = "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/"


def _ranks(*entries: dict[str, object]) -> dict:
    return {"response": {"ranks": list(entries)}}


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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_PLAYER_COUNT_URL).mock(return_value=httpx.Response(500))
    source = _source([440])
    # 500 after retries must be swallowed — no exception escapes fetch.
    events = await _collect(source, FetchContext())
    assert events == []


@respx.mock
async def test_player_count_per_app_error_does_not_abort_remaining() -> None:
    """A per-app transport error must skip only that appid, not starve the sweep.

    Regression: the loop used to ``return`` on any upstream error, so one failing
    appid stopped player-count sampling for every appid after it — the core
    popularity signal silently decayed for most tracked games.
    """
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))

    def _by_appid(request: httpx.Request) -> httpx.Response:
        # The first tracked appid errors at the transport level; the rest succeed.
        if request.url.params.get("appid") == "440":
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json={"response": {"player_count": 5, "result": 1}})

    respx.get(_PLAYER_COUNT_URL).mock(side_effect=_by_appid)
    source = _source([440, 570, 730])
    events = await _collect(source, FetchContext())
    players = [e for e in events if e.kind is EventKind.PLAYER_COUNT]
    # 440 skipped, but 570 and 730 still sampled.
    assert sorted(e.platform_app_id for e in players) == [570, 730]


@respx.mock
async def test_player_count_rate_limit_stops_sweep() -> None:
    """A sustained 429 (RetryableStatus after retries) still stops the sweep so we
    don't hammer Steam — the intentional backoff, distinct from a per-app error."""
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_PLAYER_COUNT_URL).mock(return_value=httpx.Response(429))
    source = _source([440, 570])
    events = await _collect(source, FetchContext())
    assert [e for e in events if e.kind is EventKind.PLAYER_COUNT] == []


# --- Key-authenticated, paginated catalog path (IStoreService/GetAppList/v1) -------


@respx.mock
async def test_paginated_app_list_emits_game_events(monkeypatch: pytest.MonkeyPatch) -> None:
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(200, json=_ranks()))
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


# --- Top-charts phase (ISteamChartsService/GetMostPlayedGames/v1) ------------------


@respx.mock
async def test_most_played_emits_samples_and_tracking_events() -> None:
    """Each rank entry yields a hour-truncated PLAYER_COUNT sample plus a GAME event
    that flags the app tracked and carries no name (must not clobber existing names)."""
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_MOST_PLAYED_URL).mock(
        return_value=httpx.Response(
            200,
            json=_ranks(
                {"rank": 1, "appid": 730, "concurrent_in_game": 900000},
                {"rank": 2, "appid": 570, "concurrent_in_game": 600000},
            ),
        )
    )
    source = _source([])  # no tracked games -> per-app phase is a no-op
    events = await _collect(source, FetchContext())

    pc = [e for e in events if e.kind is EventKind.PLAYER_COUNT]
    assert [e.payload for e in pc] == [{"players": 900000}, {"players": 600000}]
    for e in pc:
        _appid_part, iso_hour = e.natural_key.split(":", 1)
        parsed = datetime.fromisoformat(iso_hour)
        assert e.natural_key == f"{e.platform_app_id}:{iso_hour}"
        assert (parsed.minute, parsed.second, parsed.microsecond) == (0, 0, 0)
        assert e.occurred_at == parsed

    game_events = [e for e in events if e.kind is EventKind.GAME]
    assert [e.natural_key for e in game_events] == ["730", "570"]
    for e in game_events:
        assert e.payload == {"tracked": True}
        assert "name" not in e.payload


@respx.mock
async def test_most_played_degrades_and_per_app_phase_still_runs() -> None:
    """A 404/500 on the charts endpoint degrades; the per-app phase still polls."""
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_MOST_PLAYED_URL).mock(return_value=httpx.Response(500))
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 11, "result": 1}})
    )
    source = _source([440])
    events = await _collect(source, FetchContext())

    assert [e for e in events if e.kind is EventKind.GAME] == []
    assert [e.payload for e in events if e.kind is EventKind.PLAYER_COUNT] == [{"players": 11}]


@respx.mock
async def test_per_app_phase_skips_charted_appids() -> None:
    """An appid sampled from the charts is not polled again by the per-app phase."""
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_MOST_PLAYED_URL).mock(
        return_value=httpx.Response(
            200, json=_ranks({"rank": 1, "appid": 730, "concurrent_in_game": 5000})
        )
    )
    per_app = respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 42, "result": 1}})
    )
    # 730 is charted (skipped); 999 is tracked-only and still polled per-app.
    source = _source([730, 999])
    events = await _collect(source, FetchContext())

    pc = [e for e in events if e.kind is EventKind.PLAYER_COUNT]
    # One from charts (730), one from per-app (999) — the charted 730 is not re-polled.
    assert {e.platform_app_id for e in pc} == {730, 999}
    assert [e.payload for e in pc] == [{"players": 5000}, {"players": 42}]
    # Per-app endpoint hit exactly once (for 999 only).
    assert per_app.call_count == 1
    assert per_app.calls.last.request.url.params.get("appid") == "999"


@respx.mock
async def test_limit_honoured_across_phases() -> None:
    """ctx.limit caps total events across app-list + charts + per-app phases."""
    respx.get(_APP_LIST_URL).mock(return_value=httpx.Response(200, json={"applist": {"apps": []}}))
    respx.get(_MOST_PLAYED_URL).mock(
        return_value=httpx.Response(
            200,
            json=_ranks(
                {"rank": 1, "appid": 1, "concurrent_in_game": 10},
                {"rank": 2, "appid": 2, "concurrent_in_game": 20},
                {"rank": 3, "appid": 3, "concurrent_in_game": 30},
            ),
        )
    )
    source = _source([])
    # Each charted app emits 2 events (GAME + PLAYER_COUNT); limit=3 stops mid-charts.
    events = await _collect(source, FetchContext(limit=3))
    assert len(events) == 3


@respx.mock
async def test_small_limit_still_reaches_signal_phases_keyless() -> None:
    """Regression: a still-syncing catalog must not starve the signal phases.

    With a small ``ctx.limit`` and a catalog large enough to swallow it whole, the
    top-charts (tracked-flagging) and per-app player-count phases must still run —
    they emit *before* the catalog, which only gets the leftover budget.
    """
    # A large keyless catalog — on the old ordering this alone ate the whole limit.
    apps = [{"appid": i, "name": f"g{i}"} for i in range(1, 51)]
    respx.get(_APP_LIST_URL).mock(
        return_value=httpx.Response(200, json={"applist": {"apps": apps}})
    )
    respx.get(_MOST_PLAYED_URL).mock(
        return_value=httpx.Response(
            200, json=_ranks({"rank": 1, "appid": 730, "concurrent_in_game": 5000})
        )
    )
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 42, "result": 1}})
    )
    # 730 is charted (skipped by the per-app phase); 999 is tracked-only.
    source = _source([730, 999])
    ctx = FetchContext(limit=5)
    events = await _collect(source, ctx)

    assert len(events) == 5
    # Signal phases ran despite the huge catalog: a tracking GAME event (730), two
    # player-count samples (charts 730 + per-app 999)...
    tracking = [e for e in events if e.kind is EventKind.GAME and e.payload == {"tracked": True}]
    assert [e.platform_app_id for e in tracking] == [730]
    pc = [e for e in events if e.kind is EventKind.PLAYER_COUNT]
    assert {e.platform_app_id for e in pc} == {730, 999}
    # ...and the catalog got only the leftover budget (5 - 1 tracking - 2 samples = 2).
    catalog = [e for e in events if e.kind is EventKind.GAME and "name" in e.payload]
    assert [e.natural_key for e in catalog] == ["1", "2"]


@respx.mock
async def test_small_limit_still_reaches_signal_phases_paginated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same regression on the key-authenticated paginated catalog path."""
    _set_key(monkeypatch)
    respx.get(_STORE_APP_LIST_URL).mock(
        return_value=httpx.Response(
            200,
            json=_store_page(
                [{"appid": i, "name": f"g{i}"} for i in range(1, 51)],
                last_appid=50,
                have_more=True,
            ),
        )
    )
    respx.get(_MOST_PLAYED_URL).mock(
        return_value=httpx.Response(
            200, json=_ranks({"rank": 1, "appid": 730, "concurrent_in_game": 5000})
        )
    )
    respx.get(_PLAYER_COUNT_URL).mock(
        return_value=httpx.Response(200, json={"response": {"player_count": 42, "result": 1}})
    )
    source = _source([730, 999])
    ctx = FetchContext(limit=5)
    events = await _collect(source, ctx)

    assert len(events) == 5
    tracking = [e for e in events if e.kind is EventKind.GAME and e.payload == {"tracked": True}]
    assert [e.platform_app_id for e in tracking] == [730]
    pc = [e for e in events if e.kind is EventKind.PLAYER_COUNT]
    assert {e.platform_app_id for e in pc} == {730, 999}
    catalog = [e for e in events if e.kind is EventKind.GAME and "name" in e.payload]
    # Catalog gets the leftover budget and its cursor advances only that far.
    assert [e.natural_key for e in catalog] == ["1", "2"]
    assert ctx.cursor["last_appid"] == 2
