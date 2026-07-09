"""Unit tests for the Twitch Helix source adapter (no live API, no DB).

respx mocks the OAuth token endpoint and the Helix top-games / streams endpoints.
Credentials are injected via env vars (monkeypatched) so ``settings.twitch.enabled``
flips on/off. The Steam-appid resolver is injected to avoid the database.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from gamer.config import get_settings
from gamer.sources import REGISTRY
from gamer.sources.base import EventKind, FetchContext, RawEvent, Source
from gamer.sources.twitch import TwitchSource

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_TOP_GAMES_URL = "https://api.twitch.tv/helix/games/top"
_STREAMS_URL = "https://api.twitch.tv/helix/streams"


@pytest.fixture
def twitch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_TWITCH__CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GAMER_TWITCH__CLIENT_SECRET", "test-client-secret")
    get_settings.cache_clear()


def _source(*, appid_resolver: object = None) -> TwitchSource:
    return TwitchSource(
        appid_resolver=appid_resolver or (lambda name: None),
        rate=1000,
        per=1.0,
        max_attempts=2,
    )


async def _collect(source: TwitchSource, ctx: FetchContext) -> list[RawEvent]:
    return [e async for e in source.fetch(ctx)]


def _mock_token() -> None:
    respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "app-token-xyz", "expires_in": 3600})
    )


def test_source_satisfies_protocol() -> None:
    assert isinstance(TwitchSource(appid_resolver=lambda name: None), Source)


def test_registered_in_registry() -> None:
    assert "twitch" in REGISTRY
    assert REGISTRY["twitch"]().name == "twitch"


async def test_no_credentials_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # No env → client_id/secret empty → settings.twitch.enabled is False.
    monkeypatch.delenv("GAMER_TWITCH__CLIENT_ID", raising=False)
    monkeypatch.delenv("GAMER_TWITCH__CLIENT_SECRET", raising=False)
    get_settings.cache_clear()
    assert get_settings().twitch.enabled is False

    events = await _collect(_source(), FetchContext())
    assert events == []


@respx.mock
async def test_viewers_parsed_to_twitch_events(twitch_env: None) -> None:
    _mock_token()
    respx.get(_TOP_GAMES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "32982", "name": "Grand Theft Auto V"},
                    {"id": "21779", "name": "League of Legends"},
                ]
            },
        )
    )

    def _streams(request: httpx.Request) -> httpx.Response:
        game_id = request.url.params.get("game_id")
        counts = {"32982": [1000, 500], "21779": [2000]}
        data = [{"viewer_count": c} for c in counts.get(game_id, [])]
        return httpx.Response(200, json={"data": data})

    respx.get(_STREAMS_URL).mock(side_effect=_streams)

    events = await _collect(_source(), FetchContext())
    twitch = [e for e in events if e.kind is EventKind.TWITCH]
    assert len(twitch) == 2

    by_id = {e.payload["twitch_game_id"]: e for e in twitch}
    gta = by_id["32982"]
    assert gta.payload["viewers"] == 1500  # summed across live streams
    assert gta.payload["game_name"] == "Grand Theft Auto V"
    assert by_id["21779"].payload["viewers"] == 2000

    # natural_key = "{twitch_game_id}:{iso_hour}", hour-truncated + tz-aware.
    game_part, iso_hour = gta.natural_key.split(":", 1)
    assert game_part == "32982"
    parsed = datetime.fromisoformat(iso_hour)
    assert parsed.tzinfo is not None
    assert (parsed.minute, parsed.second, parsed.microsecond) == (0, 0, 0)
    # No resolver mapping → platform_app_id stays None, name in payload.
    assert gta.platform_app_id is None


@respx.mock
async def test_token_fetched_once_and_cached(twitch_env: None) -> None:
    token_route = respx.post(_TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "app-token-xyz", "expires_in": 3600})
    )
    respx.get(_TOP_GAMES_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    source = _source()
    await _collect(source, FetchContext())
    await _collect(source, FetchContext())

    # Two fetch runs, but the token is cached in-process → fetched exactly once.
    assert token_route.call_count == 1


@respx.mock
async def test_appid_resolver_sets_platform_app_id(twitch_env: None) -> None:
    _mock_token()
    respx.get(_TOP_GAMES_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "32982", "name": "Grand Theft Auto V"}]}
        )
    )
    respx.get(_STREAMS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"viewer_count": 42}]})
    )

    def _resolver(name: str) -> int | None:
        return 271590 if name == "Grand Theft Auto V" else None

    events = await _collect(_source(appid_resolver=_resolver), FetchContext())
    twitch = [e for e in events if e.kind is EventKind.TWITCH]
    assert len(twitch) == 1
    assert twitch[0].platform_app_id == 271590


@respx.mock
async def test_token_429_is_handled_gracefully(twitch_env: None) -> None:
    respx.post(_TOKEN_URL).mock(return_value=httpx.Response(429))
    # Top-games should never be called if the token fetch fails.
    top = respx.get(_TOP_GAMES_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    events = await _collect(_source(), FetchContext())
    assert events == []
    assert top.call_count == 0


@respx.mock
async def test_top_games_429_is_handled_gracefully(twitch_env: None) -> None:
    _mock_token()
    respx.get(_TOP_GAMES_URL).mock(return_value=httpx.Response(429))

    # 429 after retries must be swallowed — no exception escapes fetch.
    events = await _collect(_source(), FetchContext())
    assert events == []


@respx.mock
async def test_streams_500_skips_game(twitch_env: None) -> None:
    _mock_token()
    respx.get(_TOP_GAMES_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "1", "name": "Good Game"},
                    {"id": "2", "name": "Bad Game"},
                ]
            },
        )
    )

    def _streams(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("game_id") == "2":
            return httpx.Response(500)
        return httpx.Response(200, json={"data": [{"viewer_count": 10}]})

    respx.get(_STREAMS_URL).mock(side_effect=_streams)

    events = await _collect(_source(), FetchContext())
    twitch = [e for e in events if e.kind is EventKind.TWITCH]
    # The 500 game is skipped; the healthy one still emits.
    assert [e.payload["twitch_game_id"] for e in twitch] == ["1"]


@respx.mock
async def test_limit_is_honoured(twitch_env: None) -> None:
    _mock_token()
    respx.get(_TOP_GAMES_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": str(i), "name": f"g{i}"} for i in range(5)]}
        )
    )
    respx.get(_STREAMS_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"viewer_count": 1}]})
    )

    events = await _collect(_source(), FetchContext(limit=2))
    assert len([e for e in events if e.kind is EventKind.TWITCH]) == 2
