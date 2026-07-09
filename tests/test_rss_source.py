from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from gamer.config import get_settings
from gamer.sources import REGISTRY
from gamer.sources.base import EventKind, FetchContext, RawEvent, Source
from gamer.sources.rss import RssSource

FEED_A = "https://feed-a.example/rss"
FEED_B = "https://feed-b.example/atom"

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed A</title>
    <item>
      <title>Newest Story</title>
      <link>https://feed-a.example/newest</link>
      <guid>guid-newest</guid>
      <description>The newest thing happened.</description>
      <pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Older Story</title>
      <link>https://feed-a.example/older</link>
      <guid>guid-older</guid>
      <description>An older thing.</description>
      <pubDate>Tue, 07 Jul 2026 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""

ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Feed B</title>
  <entry>
    <title>Atom Entry</title>
    <link href="https://feed-b.example/entry-1"/>
    <id>atom-id-1</id>
    <summary>Atom body.</summary>
    <updated>2026-07-08T12:00:00Z</updated>
  </entry>
</feed>
"""

# No <guid>/<id>: forces the link+title hash fallback for the natural key.
RSS_NO_ID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Feed C</title>
    <item>
      <title>Id-less Story</title>
      <link>https://feed-a.example/no-id</link>
      <description>No guid here.</description>
      <pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


def _source(feeds: list[str]) -> RssSource:
    return RssSource(feeds=feeds, rate=1000, per=1.0, max_attempts=2)


async def _collect(source: RssSource, ctx: FetchContext) -> list[RawEvent]:
    return [e async for e in source.fetch(ctx)]


def test_source_satisfies_protocol() -> None:
    assert isinstance(RssSource(feeds=[]), Source)


def test_registered_in_registry() -> None:
    assert "rss" in REGISTRY
    assert REGISTRY["rss"]().name == "rss"


@respx.mock
async def test_entries_become_news_events() -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=RSS_XML.encode()))
    events = await _collect(_source([FEED_A]), FetchContext())

    assert len(events) == 2
    assert all(e.kind is EventKind.NEWS for e in events)
    first = events[0]
    assert first.source == "rss"
    assert first.natural_key == "guid-newest"
    assert first.platform_app_id is None
    assert first.payload["title"] == "Newest Story"
    assert first.payload["url"] == "https://feed-a.example/newest"
    assert first.payload["body"] == "The newest thing happened."
    # published_at parsed to aware UTC and mirrored into occurred_at.
    published = datetime.fromisoformat(first.payload["published_at"])
    assert published.tzinfo is not None
    assert first.occurred_at == published
    assert published.year == 2026 and published.hour == 10
    assert first.dedup_key() == "rss:news:guid-newest"


@respx.mock
async def test_atom_feed_parses() -> None:
    respx.get(FEED_B).mock(return_value=httpx.Response(200, content=ATOM_XML.encode()))
    events = await _collect(_source([FEED_B]), FetchContext())

    assert len(events) == 1
    event = events[0]
    assert event.natural_key == "atom-id-1"
    assert event.payload["url"] == "https://feed-b.example/entry-1"
    assert event.payload["body"] == "Atom body."
    published = datetime.fromisoformat(event.payload["published_at"])
    assert (published.year, published.month, published.hour) == (2026, 7, 12)


@respx.mock
async def test_missing_id_hashes_link_and_title() -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=RSS_NO_ID_XML.encode()))
    events = await _collect(_source([FEED_A]), FetchContext())

    assert len(events) == 1
    # Stable hash-based natural key when the feed omits guid/id.
    assert events[0].natural_key.startswith("sha256:")


@respx.mock
async def test_cursor_dedups_on_second_run() -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=RSS_XML.encode()))
    source = _source([FEED_A])
    ctx = FetchContext()

    first_run = await _collect(source, ctx)
    assert len(first_run) == 2
    # Cursor advanced to the newest entry.
    assert ctx.cursor["feeds"][FEED_A]["last_id"] == "guid-newest"

    # Second run over the same (unchanged) feed emits nothing.
    second_run = await _collect(source, ctx)
    assert second_run == []


@respx.mock
async def test_cursor_emits_only_new_entries() -> None:
    # First run sees only the older item; then the feed grows a newer item.
    older_only = """<?xml version="1.0"?><rss version="2.0"><channel><title>A</title>
    <item><title>Older</title><link>https://feed-a.example/older</link>
    <guid>guid-older</guid><pubDate>Tue, 07 Jul 2026 09:00:00 GMT</pubDate></item>
    </channel></rss>"""

    route = respx.get(FEED_A)
    route.mock(return_value=httpx.Response(200, content=older_only.encode()))
    source = _source([FEED_A])
    ctx = FetchContext()

    first = await _collect(source, ctx)
    assert [e.natural_key for e in first] == ["guid-older"]

    # Feed now has newest-first ordering with a fresh top entry.
    route.mock(return_value=httpx.Response(200, content=RSS_XML.encode()))
    second = await _collect(source, ctx)
    # Stops at the already-seen "guid-older": only the newer entry is emitted.
    assert [e.natural_key for e in second] == ["guid-newest"]


@respx.mock
async def test_limit_is_honoured_across_feeds() -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=RSS_XML.encode()))
    respx.get(FEED_B).mock(return_value=httpx.Response(200, content=ATOM_XML.encode()))
    events = await _collect(_source([FEED_A, FEED_B]), FetchContext(limit=1))
    assert len(events) == 1
    assert events[0].natural_key == "guid-newest"


async def test_disabled_emits_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_RSS__ENABLED", "false")
    get_settings.cache_clear()
    # Even with feeds configured, disabled short-circuits with zero events.
    events = await _collect(_source([FEED_A]), FetchContext())
    assert events == []


@respx.mock
async def test_reads_feeds_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_RSS__FEEDS", FEED_A)
    get_settings.cache_clear()
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=RSS_XML.encode()))
    # feeds=None -> the source reads get_settings().rss.feeds.
    events = await _collect(RssSource(rate=1000, per=1.0, max_attempts=2), FetchContext())
    assert [e.natural_key for e in events] == ["guid-newest", "guid-older"]


@respx.mock
async def test_feed_500_is_skipped_without_raising() -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(500))
    respx.get(FEED_B).mock(return_value=httpx.Response(200, content=ATOM_XML.encode()))
    # The 500 feed is skipped; the healthy feed still yields.
    events = await _collect(_source([FEED_A, FEED_B]), FetchContext())
    assert [e.natural_key for e in events] == ["atom-id-1"]


@respx.mock
async def test_malformed_xml_is_skipped_without_raising() -> None:
    respx.get(FEED_A).mock(return_value=httpx.Response(200, content=b"<rss><this is not valid xml"))
    respx.get(FEED_B).mock(return_value=httpx.Response(200, content=ATOM_XML.encode()))
    events = await _collect(_source([FEED_A, FEED_B]), FetchContext())
    # Malformed feed contributes nothing; the good feed still parses.
    assert [e.natural_key for e in events] == ["atom-id-1"]
