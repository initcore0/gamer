from __future__ import annotations

from datetime import UTC, datetime

import httpx
import respx

from gamer.sources import REGISTRY
from gamer.sources.base import EventKind, FetchContext, RawEvent, Source
from gamer.sources.steam_store import (
    APPDETAILS_URL,
    NEWS_URL,
    StaticCrawlTarget,
    SteamStoreSource,
)

# ── Sample fixtures (realistic Steam JSON shapes) ─────────────────────────────

APPDETAILS_OK = {
    "440": {
        "success": True,
        "data": {
            "name": "Team Fortress 2",
            "is_free": True,
            "genres": [
                {"id": "1", "description": "Action"},
                {"id": "37", "description": "Free to Play"},
            ],
            "release_date": {"coming_soon": False, "date": "10 Oct, 2007"},
        },
    }
}

APPDETAILS_PAID = {
    "620": {
        "success": True,
        "data": {
            "name": "Portal 2",
            "is_free": False,
            "genres": [
                {"id": "1", "description": "Action"},
                {"id": "25", "description": "Adventure"},
            ],
            "price_overview": {"currency": "USD", "initial": 999, "final": 499},
            "release_date": {"coming_soon": False, "date": "18 Apr, 2011"},
        },
    }
}

APPDETAILS_FAILURE = {"999999": {"success": False}}

NEWS_OK = {
    "appnews": {
        "appid": 440,
        "newsitems": [
            {
                "gid": "5764655836423433690",
                "title": "Team Fortress 2 Update Released",
                "url": "https://store.steampowered.com/news/440/1",
                "author": "Valve",
                "contents": "We've released a new update for TF2.",
                "feedlabel": "Community Announcements",
                "date": 1700000000,
                "feedname": "steam_community_announcements",
            },
            {
                "gid": "5764655836423433111",
                "title": "Older news",
                "url": "https://store.steampowered.com/news/440/2",
                "contents": "Older body",
                "date": 1699000000,
            },
        ],
    }
}

REVIEWS_OK = {
    "success": 1,
    "query_summary": {
        "num_reviews": 0,
        "review_score": 9,
        "review_score_desc": "Overwhelmingly Positive",
        "total_positive": 900000,
        "total_negative": 50000,
        "total_reviews": 950000,
    },
}


async def _collect(source: Source, ctx: FetchContext) -> list[RawEvent]:
    return [e async for e in source.fetch(ctx)]


# ── Registration ──────────────────────────────────────────────────────────────


def test_steam_store_is_registered() -> None:
    assert "steam_store" in REGISTRY
    src = REGISTRY["steam_store"]()
    assert isinstance(src, Source)
    assert src.name == "steam_store"


# ── appdetails → GAME ─────────────────────────────────────────────────────────


@respx.mock
async def test_appdetails_free_game_parsed() -> None:
    respx.get(APPDETAILS_URL, params={"appids": "440", "l": "en"}).mock(
        return_value=httpx.Response(200, json=APPDETAILS_OK)
    )
    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([440]),
        fetch_news=False,
        fetch_reviews=False,
    )
    events = await _collect(source, FetchContext())

    assert len(events) == 1
    ev = events[0]
    assert ev.kind is EventKind.GAME
    assert ev.natural_key == "440"
    assert ev.platform_app_id == 440
    assert ev.payload["name"] == "Team Fortress 2"
    assert ev.payload["genres"] == ["Action", "Free to Play"]
    assert ev.payload["is_free"] is True
    assert ev.payload["price_cents"] is None
    assert ev.payload["release_date"] == datetime(2007, 10, 10, tzinfo=UTC).isoformat()


@respx.mock
async def test_appdetails_paid_game_price_and_genres() -> None:
    respx.get(APPDETAILS_URL, params={"appids": "620", "l": "en"}).mock(
        return_value=httpx.Response(200, json=APPDETAILS_PAID)
    )
    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([620]),
        fetch_news=False,
        fetch_reviews=False,
    )
    events = await _collect(source, FetchContext())

    assert len(events) == 1
    ev = events[0]
    assert ev.payload["is_free"] is False
    assert ev.payload["price_cents"] == 499
    assert ev.payload["genres"] == ["Action", "Adventure"]
    assert ev.payload["release_date"] == datetime(2011, 4, 18, tzinfo=UTC).isoformat()


@respx.mock
async def test_appdetails_success_false_emits_unavailable_marker() -> None:
    """A success:false response must still emit a GAME marker so the sink stamps
    details_fetched_at — otherwise the NULLS-FIRST crawl queue re-selects the same
    delisted appid every run and starves real games. No metadata is set, but the
    marker moves the appid out of the queue head."""
    respx.get(APPDETAILS_URL, params={"appids": "999999", "l": "en"}).mock(
        return_value=httpx.Response(200, json=APPDETAILS_FAILURE)
    )
    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([999999]),
        fetch_news=False,
        fetch_reviews=False,
    )
    events = await _collect(source, FetchContext())
    assert len(events) == 1
    ev = events[0]
    assert ev.kind is EventKind.GAME
    assert ev.platform_app_id == 999999
    assert ev.payload == {"details_unavailable": True}


# ── news → NEWS ───────────────────────────────────────────────────────────────


@respx.mock
async def test_news_events_gid_and_published_at() -> None:
    respx.get(APPDETAILS_URL).mock(return_value=httpx.Response(200, json=APPDETAILS_OK))
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, json=NEWS_OK))

    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([440]),
        fetch_news=True,
        fetch_reviews=False,
    )
    ctx = FetchContext()
    events = await _collect(source, ctx)

    news = [e for e in events if e.kind is EventKind.NEWS]
    assert len(news) == 2
    first = news[0]
    assert first.natural_key == "5764655836423433690"
    assert first.dedup_key() == "steam_store:news:5764655836423433690"
    assert first.payload["title"] == "Team Fortress 2 Update Released"
    assert first.payload["body"] == "We've released a new update for TF2."
    expected = datetime.fromtimestamp(1700000000, tz=UTC)
    assert first.occurred_at == expected
    assert first.payload["published_at"] == expected.isoformat()

    # Cursor records the newest gid so a re-run skips already-seen items.
    assert ctx.cursor["news_gids"]["440"] == "5764655836423433690"


@respx.mock
async def test_news_cursor_skips_already_seen() -> None:
    respx.get(APPDETAILS_URL).mock(return_value=httpx.Response(200, json=APPDETAILS_OK))
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, json=NEWS_OK))

    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([440]),
        fetch_news=True,
        fetch_reviews=False,
    )
    # Pretend we already saw the newest item last run.
    ctx = FetchContext(cursor={"news_gids": {"440": "5764655836423433690"}})
    events = await _collect(source, ctx)
    assert [e for e in events if e.kind is EventKind.NEWS] == []


# ── reviews → REVIEW ──────────────────────────────────────────────────────────


@respx.mock
async def test_reviews_count_event() -> None:
    respx.get(APPDETAILS_URL).mock(return_value=httpx.Response(200, json=APPDETAILS_OK))
    respx.get("https://store.steampowered.com/appreviews/440").mock(
        return_value=httpx.Response(200, json=REVIEWS_OK)
    )
    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([440]),
        fetch_news=False,
        fetch_reviews=True,
    )
    events = await _collect(source, FetchContext())
    reviews = [e for e in events if e.kind is EventKind.REVIEW]
    assert len(reviews) == 1
    assert reviews[0].payload == {"review_count": 950000}
    assert reviews[0].natural_key == "440"


# ── rate limiting / graceful degradation ──────────────────────────────────────


@respx.mock
async def test_429_handled_gracefully() -> None:
    # appdetails keeps returning 429; retries are exhausted and the run stops
    # without raising (Source contract: degrade, don't crash).
    respx.get(APPDETAILS_URL).mock(return_value=httpx.Response(429))
    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([440]),
        fetch_news=False,
        fetch_reviews=False,
        rate=1000,
        per=1.0,
        max_attempts=2,
    )
    events = await _collect(source, FetchContext())
    assert events == []


@respx.mock
async def test_limit_is_honoured() -> None:
    respx.get(APPDETAILS_URL).mock(return_value=httpx.Response(200, json=APPDETAILS_OK))
    respx.get(NEWS_URL).mock(return_value=httpx.Response(200, json=NEWS_OK))
    source = SteamStoreSource(
        crawl_target=StaticCrawlTarget([440, 620, 730]),
        fetch_news=True,
        fetch_reviews=False,
    )
    events = await _collect(source, FetchContext(limit=1))
    assert len(events) == 1
