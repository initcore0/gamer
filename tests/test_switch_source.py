"""Switch eShop source tests — prove the platform abstraction (PLAN.md §6 M5).

Unit tests respx-mock the feed with a canned fixture and assert entries become
RELEASE events carrying ``platform="switch"`` and a stable natural_key /
platform_app_id; disabled → no events; malformed/500 handled gracefully; cursor
dedups on a second run.

The ``@pytest.mark.integration`` test is the money test: it runs the source
through the SHARED (Steam-default) ``DbEventSink`` against a real DB and asserts
the resulting ``Game`` rows have ``platform == Platform.SWITCH`` — proving one
sink persists multiple platforms end-to-end.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from gamer.config import get_settings
from gamer.sources import REGISTRY
from gamer.sources.base import EventKind, FetchContext, RawEvent, Source
from gamer.sources.switch import SwitchSource, _stable_int_id

FEED = "https://switch-feed.example/releases.json"
RSS_FEED = "https://switch-feed.example/releases.rss"

# Canned JSON feed: two titles, one with a numeric nsuid, dated newest-first.
JSON_FEED = json.dumps(
    {
        "games": [
            {
                "nsuid": 70010000512345,
                "name": "Hollow Knight: Silksong",
                "release_date": "2026-07-08",
                "genres": ["Metroidvania", "Action"],
            },
            {
                "id": "coffee-talk-2",
                "name": "Coffee Talk 2",
                "release_date": "2026-07-01",
                "genres": "Visual Novel",
            },
        ]
    }
)

# A tolerant top-level list shape with an id-less title (slug-hash fallback).
JSON_LIST_FEED = json.dumps(
    [
        {"name": "Mystery Indie Game", "release_date": "2026-07-05"},
    ]
)

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>eShop Releases</title>
    <item>
      <title>Pixel Platformer</title>
      <guid>eshop-9001</guid>
      <pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def _source(feed_url: str = FEED) -> SwitchSource:
    return SwitchSource(feed_url=feed_url, rate=1000, per=1.0, max_attempts=2)


async def _collect(source: SwitchSource, ctx: FetchContext) -> list[RawEvent]:
    return [e async for e in source.fetch(ctx)]


@pytest.fixture(autouse=True)
def _enable_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switch is disabled by default; enable it for the unit tests here."""
    monkeypatch.setenv("GAMER_SWITCH__ENABLED", "true")
    get_settings.cache_clear()


def test_source_satisfies_protocol() -> None:
    assert isinstance(SwitchSource(feed_url=""), Source)


def test_registered_in_registry() -> None:
    assert "switch" in REGISTRY
    source = REGISTRY["switch"]()
    assert source.name == "switch"
    assert source.default_interval_seconds == 6 * 3600


@respx.mock
async def test_entries_become_switch_release_events() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=JSON_FEED.encode()))
    events = await _collect(_source(), FetchContext())

    assert len(events) == 2
    assert all(e.kind is EventKind.RELEASE for e in events)
    # CRUCIAL: every event carries platform="switch" — the seam under test.
    assert all(e.platform == "switch" for e in events)
    assert all(e.source == "switch" for e in events)

    first = events[0]
    assert first.natural_key == "70010000512345"
    # Numeric nsuid flows straight through to platform_app_id.
    assert first.platform_app_id == 70010000512345
    assert first.payload["name"] == "Hollow Knight: Silksong"
    assert first.payload["genres"] == ["Metroidvania", "Action"]
    assert first.payload["release_date"].startswith("2026-07-08")
    assert first.dedup_key() == "switch:release:70010000512345"

    second = events[1]
    assert second.natural_key == "coffee-talk-2"
    # Non-numeric id → stable hash surrogate for platform_app_id.
    assert second.platform_app_id == _stable_int_id("coffee-talk-2", "Coffee Talk 2")
    # Comma-string genres are coerced to a list.
    assert second.payload["genres"] == ["Visual Novel"]


@respx.mock
async def test_idless_title_hashes_slug_stably() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=JSON_LIST_FEED.encode()))
    events = await _collect(_source(), FetchContext())

    assert len(events) == 1
    event = events[0]
    assert event.platform == "switch"
    # No feed id → slug-based natural key and a stable integer platform_app_id.
    assert event.natural_key == "slug:mystery-indie-game"
    assert event.platform_app_id == _stable_int_id(None, "Mystery Indie Game")
    # Deterministic: recomputing yields the same id.
    assert _stable_int_id(None, "Mystery Indie Game") == _stable_int_id(None, "Mystery Indie Game")


@respx.mock
async def test_rss_feed_fallback_parses() -> None:
    respx.get(RSS_FEED).mock(return_value=httpx.Response(200, content=RSS_XML.encode()))
    events = await _collect(_source(RSS_FEED), FetchContext())

    assert len(events) == 1
    event = events[0]
    assert event.platform == "switch"
    assert event.kind is EventKind.RELEASE
    assert event.natural_key == "eshop-9001"
    assert event.payload["name"] == "Pixel Platformer"
    assert event.payload["release_date"].startswith("2026-07-08")


@respx.mock
async def test_limit_is_honoured() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=JSON_FEED.encode()))
    events = await _collect(_source(), FetchContext(limit=1))
    assert len(events) == 1
    assert events[0].natural_key == "70010000512345"


@respx.mock
async def test_cursor_dedups_on_second_run() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=JSON_FEED.encode()))
    source = _source()
    ctx = FetchContext()

    first = await _collect(source, ctx)
    assert len(first) == 2
    # Cursor advanced to the newest (by release date) entry.
    assert ctx.cursor["last_id"] == "70010000512345"

    # Second run over the same unchanged feed emits nothing.
    second = await _collect(source, ctx)
    assert second == []


async def test_disabled_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_SWITCH__ENABLED", "false")
    get_settings.cache_clear()
    # Even with a feed_url configured, disabled short-circuits with zero events.
    events = await _collect(_source(), FetchContext())
    assert events == []


async def test_no_feed_url_emits_nothing() -> None:
    # Enabled but no feed_url → graceful no-op (no HTTP call, no raise).
    events = await _collect(_source(feed_url=""), FetchContext())
    assert events == []


async def test_reads_feed_url_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_SWITCH__FEED_URL", FEED)
    get_settings.cache_clear()
    with respx.mock:
        respx.get(FEED).mock(return_value=httpx.Response(200, content=JSON_FEED.encode()))
        # feed_url=None → the source reads get_settings().switch.feed_url.
        events = await _collect(SwitchSource(rate=1000, per=1.0, max_attempts=2), FetchContext())
    assert [e.natural_key for e in events] == ["70010000512345", "coffee-talk-2"]


@respx.mock
async def test_feed_500_is_handled_gracefully() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(500))
    events = await _collect(_source(), FetchContext())
    assert events == []


@respx.mock
async def test_malformed_feed_is_handled_gracefully() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=b"<<< not json and not xml <<<"))
    events = await _collect(_source(), FetchContext())
    assert events == []


@respx.mock
async def test_empty_json_feed_is_handled_gracefully() -> None:
    respx.get(FEED).mock(return_value=httpx.Response(200, content=b'{"games": []}'))
    events = await _collect(_source(), FetchContext())
    assert events == []


# ── The money test: end-to-end through the shared sink against a real DB ───────


@pytest.mark.integration
async def test_switch_games_persist_under_switch_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the source through the SHARED (Steam-default) DbEventSink and assert the
    resulting Game rows are ``Platform.SWITCH`` — the abstraction proven end-to-end.
    """
    from sqlalchemy import select

    from gamer.db import session_scope
    from gamer.db.models import Game, Platform
    from gamer.sources.sink import DbEventSink

    monkeypatch.setenv("GAMER_SWITCH__ENABLED", "true")
    get_settings.cache_clear()

    with respx.mock:
        respx.get(FEED).mock(return_value=httpx.Response(200, content=JSON_FEED.encode()))
        events = await _collect(_source(), FetchContext())

    assert events, "fixture should yield events"

    # The sink defaults to Steam — the events' own platform="switch" must win.
    sink = DbEventSink()
    written = await sink.persist(events)
    assert written == len(events)

    app_ids = [e.platform_app_id for e in events]
    async with session_scope() as session:
        rows = (
            (
                await session.execute(
                    select(Game).where(
                        Game.platform == Platform.SWITCH,
                        Game.platform_app_id.in_(app_ids),
                    )
                )
            )
            .scalars()
            .all()
        )

    assert {r.platform_app_id for r in rows} == set(app_ids)
    assert all(r.platform == Platform.SWITCH for r in rows)
    names = {r.name for r in rows}
    assert "Hollow Knight: Silksong" in names


def test_unknown_platform_is_skipped_not_raised() -> None:
    """The sink's contract: one bad event never aborts the batch. An event with
    an unknown platform value resolves to None (logged + skipped) instead of
    raising ValueError and rolling back every good event around it."""
    from datetime import UTC, datetime

    from gamer.db.models import Platform
    from gamer.sources.base import EventKind, RawEvent
    from gamer.sources.sink import DbEventSink

    sink = DbEventSink()

    def _event(platform: str | None) -> RawEvent:
        return RawEvent(
            source="test",
            kind=EventKind.GAME,
            natural_key="1",
            payload={"name": "X"},
            occurred_at=datetime.now(UTC),
            platform_app_id=1,
            platform=platform,
        )

    assert sink._event_platform(_event(None)) is Platform.STEAM
    assert sink._event_platform(_event("switch")) is Platform.SWITCH
    assert sink._event_platform(_event("dreamcast")) is None
