"""The ``steam_store`` source adapter — Steam Store + News (keyless endpoints).

Covers the three free, keyless Steam endpoints (PLAN.md §3):

* **appdetails** — genres / price / release date → :class:`EventKind.GAME`.
  This endpoint is *heavily* rate-limited (~200 req / 5 min, PLAN.md §7), so we
  crawl it slowly (a low-rate :class:`PoliteClient`) and cache hard by only
  refetching the stalest ``details_fetched_at`` games first.
* **GetNewsForApp** — per-app news / patch notes → :class:`EventKind.NEWS`.
* **appreviews** — review-count sample → :class:`EventKind.REVIEW`.

Design (PLAN.md §7 mitigations):

* **Slow crawl + caching + priority.** The list of appids to visit is an
  injectable :class:`CrawlTarget`. The default reads the DB for the games whose
  ``details_fetched_at`` is oldest (nulls first), so popular/tracked games that
  have never been fetched, or are staleset, are prioritised. Tests inject a
  static list and never touch the DB.
* **Degrade, never crash.** ``fetch`` swallows expected upstream failures (429,
  5xx exhaustion, malformed JSON) — it logs and stops rather than raising, per
  the :class:`~gamer.sources.base.Source` contract.
* **Idempotent cursors.** ``ctx.cursor`` carries the last-seen news gid per
  appid and a crawl checkpoint; it is mutated in place so partial progress is
  never lost.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from gamer.logging import get_logger
from gamer.sources.base import EventKind, FetchContext, RawEvent
from gamer.sources.http import PoliteClient, RetryableStatus

log = get_logger("sources.steam_store")

SOURCE_NAME = "steam_store"

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2"
REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"

# appdetails is stingy (~200 req/5min); keep well under that with a low rate.
_DEFAULT_RATE = 40
_DEFAULT_PER = 60.0
_NEWS_COUNT = 20
_NEWS_MAXLENGTH = 2000

# Steam's ``release_date.date`` is free text; these cover the common English
# store formats ("9 Nov, 2020", "Nov 9, 2020", "2020"). Anything else → None.
_RELEASE_DATE_FORMATS = ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%Y")


class CrawlTarget(Protocol):
    """Supplies the appids a run should visit, most-stale first.

    Injecting this keeps the adapter a pure function of its inputs: unit tests
    pass a :class:`StaticCrawlTarget`; production uses :class:`DbCrawlTarget`
    which prioritises the stalest ``details_fetched_at`` (PLAN.md §7).
    """

    async def appids(self, limit: int) -> Sequence[int]:
        """Return up to ``limit`` appids to crawl this run, in priority order."""
        ...


class StaticCrawlTarget:
    """A fixed appid list — the DB-free target used by unit tests."""

    def __init__(self, appids: Sequence[int]) -> None:
        self._appids = list(appids)

    async def appids(self, limit: int) -> Sequence[int]:
        return self._appids[:limit]


class DbCrawlTarget:
    """Reads the games whose ``details_fetched_at`` is oldest (nulls first).

    Never-fetched games (NULL) come first, then the staleset — this is the
    priority queue from PLAN.md §7 without polling every game every run.
    """

    async def appids(self, limit: int) -> Sequence[int]:
        # Imported lazily so DB-free unit tests never import the ORM/engine.
        from sqlalchemy import select

        from gamer.db import session_scope
        from gamer.db.models import Game

        async with session_scope() as session:
            stmt = (
                select(Game.platform_app_id)
                .order_by(Game.details_fetched_at.asc().nulls_first())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return list(rows)


def _to_utc(unix_seconds: int) -> datetime:
    return datetime.fromtimestamp(unix_seconds, tz=UTC)


def _parse_release_date(data: dict[str, Any]) -> str | None:
    """Best-effort ISO date from appdetails ``release_date.date`` (free text)."""
    release = data.get("release_date")
    if not isinstance(release, dict):
        return None
    raw = release.get("date")
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    for fmt in _RELEASE_DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=UTC).isoformat()
    return None


def _extract_genres(data: dict[str, Any]) -> list[str]:
    genres = data.get("genres")
    if not isinstance(genres, list):
        return []
    out: list[str] = []
    for g in genres:
        if isinstance(g, dict):
            desc = g.get("description")
            if isinstance(desc, str) and desc:
                out.append(desc)
    return out


def _extract_price_cents(data: dict[str, Any]) -> int | None:
    price = data.get("price_overview")
    if not isinstance(price, dict):
        return None
    final = price.get("final")
    return final if isinstance(final, int) else None


class SteamStoreSource:
    """Steam Store + News adapter. Implements the ``Source`` protocol."""

    name = SOURCE_NAME
    # Store data changes slowly and the endpoint is stingy — run infrequently.
    default_interval_seconds = 3600

    def __init__(
        self,
        *,
        crawl_target: CrawlTarget | None = None,
        rate: int = _DEFAULT_RATE,
        per: float = _DEFAULT_PER,
        max_attempts: int = 4,
        fetch_news: bool = True,
        fetch_reviews: bool = True,
        crawl_batch: int = 50,
    ) -> None:
        self._crawl_target: CrawlTarget = crawl_target or DbCrawlTarget()
        self._rate = rate
        self._per = per
        self._max_attempts = max_attempts
        self._fetch_news = fetch_news
        self._fetch_reviews = fetch_reviews
        self._crawl_batch = crawl_batch

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        limit = ctx.limit
        # Per-appid last-seen news gid lives under cursor["news_gids"].
        news_gids: dict[str, Any] = ctx.cursor.setdefault("news_gids", {})

        target_count = limit if limit is not None else self._crawl_batch
        try:
            appids = await self._crawl_target.appids(target_count)
        except Exception as exc:  # a broken target degrades, never crashes
            log.error("crawl_target_failed", source=self.name, error=str(exc))
            return

        emitted = 0
        client = PoliteClient(
            rate=self._rate,
            per=self._per,
            max_attempts=self._max_attempts,
        )
        try:
            async with client:
                for appid in appids:
                    if limit is not None and emitted >= limit:
                        break

                    game_event = await self._fetch_details(client, appid)
                    if game_event is not None:
                        yield game_event
                        emitted += 1
                        ctx.cursor["last_appid"] = appid
                        if limit is not None and emitted >= limit:
                            break

                    if self._fetch_news:
                        async for ev in self._fetch_news_items(client, appid, news_gids):
                            yield ev
                            emitted += 1
                            if limit is not None and emitted >= limit:
                                break
                        if limit is not None and emitted >= limit:
                            break

                    if self._fetch_reviews:
                        review_event = await self._fetch_reviews_count(client, appid)
                        if review_event is not None:
                            yield review_event
                            emitted += 1
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # Retries were exhausted or the transport failed — stop gracefully.
            log.warning("steam_store_stopped", source=self.name, error=str(exc))

    async def _fetch_details(self, client: PoliteClient, appid: int) -> RawEvent | None:
        try:
            body = await client.get_json(APPDETAILS_URL, params={"appids": appid, "l": "en"})
        except httpx.HTTPStatusError as exc:
            log.warning("appdetails_http_error", appid=appid, status=exc.response.status_code)
            return None
        except (httpx.HTTPError, RetryableStatus, ValueError) as exc:
            log.warning("appdetails_failed", appid=appid, error=str(exc))
            return None

        entry = body.get(str(appid)) if isinstance(body, dict) else None
        if not isinstance(entry, dict) or not entry.get("success"):
            log.info("appdetails_unavailable", appid=appid)
            # Emit a "checked but unavailable" marker so the sink stamps
            # details_fetched_at. Otherwise this appid keeps details_fetched_at
            # NULL, and the NULLS-FIRST crawl queue re-selects it every run —
            # delisted apps clog the queue head and starve real games forever.
            return RawEvent(
                source=self.name,
                kind=EventKind.GAME,
                natural_key=str(appid),
                payload={"details_unavailable": True},
                occurred_at=datetime.now(UTC),
                platform_app_id=appid,
                fetched_at=datetime.now(UTC),
            )
        data = entry.get("data")
        if not isinstance(data, dict):
            return None

        name = data.get("name")
        if not isinstance(name, str) or not name:
            return None

        is_free = bool(data.get("is_free", False))
        price_cents = None if is_free else _extract_price_cents(data)
        payload: dict[str, Any] = {
            "name": name,
            "genres": _extract_genres(data),
            "price_cents": price_cents,
            "is_free": is_free,
            "release_date": _parse_release_date(data),
        }
        return RawEvent(
            source=self.name,
            kind=EventKind.GAME,
            natural_key=str(appid),
            payload=payload,
            occurred_at=datetime.now(UTC),
            platform_app_id=appid,
            fetched_at=datetime.now(UTC),
        )

    async def _fetch_news_items(
        self, client: PoliteClient, appid: int, news_gids: dict[str, Any]
    ) -> AsyncIterator[RawEvent]:
        try:
            body = await client.get_json(
                NEWS_URL,
                params={
                    "appid": appid,
                    "count": _NEWS_COUNT,
                    "maxlength": _NEWS_MAXLENGTH,
                },
            )
        except httpx.HTTPStatusError as exc:
            log.warning("news_http_error", appid=appid, status=exc.response.status_code)
            return
        except (httpx.HTTPError, RetryableStatus, ValueError) as exc:
            log.warning("news_failed", appid=appid, error=str(exc))
            return

        appnews = body.get("appnews") if isinstance(body, dict) else None
        if not isinstance(appnews, dict):
            return
        items = appnews.get("newsitems")
        if not isinstance(items, list):
            return

        last_seen = news_gids.get(str(appid))
        newest_gid: str | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            gid = item.get("gid")
            if gid is None:
                continue
            gid = str(gid)
            if newest_gid is None:
                newest_gid = gid  # feed is newest-first
            if last_seen is not None and gid == last_seen:
                break  # reached content we have already emitted

            date_raw = item.get("date")
            occurred_at = _to_utc(int(date_raw)) if isinstance(date_raw, int) else datetime.now(UTC)
            payload: dict[str, Any] = {
                "title": item.get("title") or "",
                "url": item.get("url"),
                "body": item.get("contents"),
                "published_at": occurred_at.isoformat(),
            }
            yield RawEvent(
                source=self.name,
                kind=EventKind.NEWS,
                natural_key=gid,
                payload=payload,
                occurred_at=occurred_at,
                platform_app_id=appid,
                fetched_at=datetime.now(UTC),
            )

        if newest_gid is not None:
            news_gids[str(appid)] = newest_gid

    async def _fetch_reviews_count(self, client: PoliteClient, appid: int) -> RawEvent | None:
        try:
            body = await client.get_json(
                REVIEWS_URL.format(appid=appid),
                params={"json": 1, "num_per_page": 0},
            )
        except httpx.HTTPStatusError as exc:
            log.warning("reviews_http_error", appid=appid, status=exc.response.status_code)
            return None
        except (httpx.HTTPError, RetryableStatus, ValueError) as exc:
            log.warning("reviews_failed", appid=appid, error=str(exc))
            return None

        summary = body.get("query_summary") if isinstance(body, dict) else None
        if not isinstance(summary, dict):
            return None
        total = summary.get("total_reviews")
        if not isinstance(total, int):
            return None
        return RawEvent(
            source=self.name,
            kind=EventKind.REVIEW,
            natural_key=str(appid),
            payload={"review_count": total},
            occurred_at=datetime.now(UTC),
            platform_app_id=appid,
            fetched_at=datetime.now(UTC),
        )
