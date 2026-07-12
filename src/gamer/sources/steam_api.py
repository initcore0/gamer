"""Steam Web API source adapter (official endpoints only — no scraping).

Covers the official Steam catalog and player-count endpoints:

* Catalog sync — the full app catalog, walked in appid order and emitted one
  :class:`EventKind.GAME` event per app (natural key = the appid). Two endpoints
  back this, chosen at runtime:

  - ``IStoreService/GetAppList/v1`` (key-authenticated, **preferred**) —
    server-side paginated via a ``last_appid`` cursor. We advance the cursor
    across runs, which fits the existing checkpoint design cleanly and avoids
    downloading the entire list each run.
  - ``ISteamApps/GetAppList/v2`` (keyless, fallback) — returns the whole list in
    one response; we sort it and checkpoint an *index* into it. Used only when no
    Steam key is configured. Note: this endpoint 404s from some networks, which is
    why the key-authenticated path is preferred whenever a key is available.

  The list is huge and mostly static, so either way we checkpoint our position in
  ``ctx.cursor`` and resume across runs, honouring ``ctx.limit`` as a soft per-run
  cap.
* ``ISteamUserStats/GetNumberOfCurrentPlayers/v1`` — the current concurrent-player
  count for a single app. We poll this for the set of *tracked* games and emit an
  :class:`EventKind.PLAYER_COUNT` sample per game (natural key = ``appid:iso_hour``
  so at most one sample per game per hour is retained by the idempotent sink).

The set of appids to poll for player counts is injectable (``appids_provider``) so
unit tests need no database; the default reads ``tracked`` games via the ORM.

Politeness and resilience come entirely from :class:`PoliteClient` (rate limit +
retry/backoff on 429/5xx). ``fetch`` never raises on expected upstream failures —
it logs and stops, per the :class:`Source` contract.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from gamer.config import get_settings
from gamer.db import session_scope
from gamer.db.models import Game
from gamer.logging import get_logger, redact_secrets
from gamer.sources.base import EventKind, FetchContext, RawEvent
from gamer.sources.http import PoliteClient, RetryableStatus

log = get_logger("sources.steam_api")

# Upstream failures that are expected and must degrade (log + stop) rather than
# crash the run: httpx transport/status errors plus PoliteClient's retry-exhausted
# 429/5xx signal.
_UPSTREAM_ERRORS = (httpx.HTTPError, RetryableStatus)

_BASE = "https://api.steampowered.com"
# Keyless full-catalog dump (fallback). 404s from some networks — see module docs.
_APP_LIST_URL = f"{_BASE}/ISteamApps/GetAppList/v2/"
# Key-authenticated, server-side-paginated catalog (preferred when a key is set).
_STORE_APP_LIST_URL = f"{_BASE}/IStoreService/GetAppList/v1/"
_PLAYER_COUNT_URL = f"{_BASE}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
# Top ~100 most-played games right now (keyless; include the key when configured).
# Provides a player-count sample per app *and* the set of appids to auto-track.
_MOST_PLAYED_URL = f"{_BASE}/ISteamChartsService/GetMostPlayedGames/v1/"

#: Provider of appids to poll for player counts. May be sync or async.
AppidsProvider = Callable[[], Sequence[int] | Awaitable[Sequence[int]]]


async def _tracked_appids() -> Sequence[int]:
    """Default provider: appids of games flagged ``tracked`` in the catalog."""
    async with session_scope() as session:
        result = await session.execute(select(Game.platform_app_id).where(Game.tracked.is_(True)))
        return [int(appid) for appid in result.scalars().all()]


class SteamApiSource:
    """Official Steam Web API adapter. Implements the :class:`Source` protocol."""

    name = "steam_api"
    default_interval_seconds = 3600

    def __init__(
        self,
        *,
        appids_provider: AppidsProvider | None = None,
        rate: int = 40,
        per: float = 60.0,
        max_attempts: int = 4,
        app_list_page_size: int = 1000,
    ) -> None:
        # Injectable so unit tests need no DB; defaults to tracked-games query.
        self._appids_provider: AppidsProvider = appids_provider or _tracked_appids
        self._rate = rate
        self._per = per
        self._max_attempts = max_attempts
        # How many catalog entries to emit per run when ctx.limit is unset.
        self._app_list_page_size = app_list_page_size

    def _client(self) -> PoliteClient:
        return PoliteClient(rate=self._rate, per=self._per, max_attempts=self._max_attempts)

    async def _resolve_appids(self) -> Sequence[int]:
        provided = self._appids_provider()
        if isinstance(provided, Awaitable):
            return await provided
        return provided

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        """Yield the signal phases first — top-charts events (player-count samples +
        tracking GAME events for the top ~100) then per-app PLAYER_COUNT events for
        tracked games — and only then the catalog sync (GAME events).

        The signal phases are small (~100 + tracked-count events) and matter more
        than raw catalog fill, so they run first and the catalog gets whatever budget
        remains. This is deliberate: the full catalog is ~250k appids and takes weeks
        to walk at the per-run page size, and if it ran first it would consume the
        entire per-run ``ctx.limit`` every run, permanently starving the signal
        phases (they'd never execute while the catalog is still syncing).

        Honours ``ctx.limit`` as an overall soft cap and mutates ``ctx.cursor`` in
        place so a huge catalog resumes across runs. Never raises on expected
        upstream failures.
        """
        emitted = 0
        # Appids already sampled from the top-charts phase this run — the per-app
        # phase skips them so we never poll the same appid twice in one run.
        charted: set[int] = set()
        async with self._client() as client:
            async for event in self._fetch_most_played(client, charted):
                emitted += 1
                yield event
                if ctx.limit is not None and emitted >= ctx.limit:
                    return

            async for event in self._fetch_player_counts(client, skip=charted):
                emitted += 1
                yield event
                if ctx.limit is not None and emitted >= ctx.limit:
                    return

            # Catalog sync last: it receives the budget the signal phases left
            # unspent (via ``already_emitted``), so a still-syncing catalog can no
            # longer starve the phases above.
            async for event in self._fetch_app_list(client, ctx, emitted):
                emitted += 1
                yield event
                if ctx.limit is not None and emitted >= ctx.limit:
                    return

    async def _fetch_app_list(
        self, client: PoliteClient, ctx: FetchContext, already_emitted: int
    ) -> AsyncIterator[RawEvent]:
        """Emit GAME events for the catalog, dispatching by whether a key is set.

        Prefers the key-authenticated, server-side-paginated ``IStoreService``
        endpoint; falls back to the keyless full-list dump when no key is
        configured. Both honour ``ctx.limit`` and checkpoint into ``ctx.cursor``.
        """
        api_key = get_settings().steam.api_key.get_secret_value()
        if api_key:
            async for event in self._fetch_app_list_paginated(
                client, ctx, already_emitted, api_key
            ):
                yield event
        else:
            async for event in self._fetch_app_list_keyless(client, ctx, already_emitted):
                yield event

    async def _fetch_app_list_paginated(
        self, client: PoliteClient, ctx: FetchContext, already_emitted: int, api_key: str
    ) -> AsyncIterator[RawEvent]:
        """Walk ``IStoreService/GetAppList/v1`` via its server-side cursor.

        Cursor keys:
          * ``last_appid`` — the highest appid seen so far; passed back to the API
            as the pagination cursor so the next run resumes past it. Reset to 0
            after a full pass so the catalog is re-walked (picking up new appids).
          * ``last_full_sync`` — ISO timestamp of the last time we reached the end.

        The per-run budget (``ctx.limit`` or ``app_list_page_size``) bounds how many
        events we emit; we request the API in pages of ``app_list_page_size`` and
        stop once the budget is spent or the API reports no more results.
        """
        if ctx.limit is not None:
            budget = max(ctx.limit - already_emitted, 0)
        else:
            budget = self._app_list_page_size
        if budget <= 0:
            return

        last_appid = int(ctx.cursor.get("last_appid", 0))
        emitted = 0
        while emitted < budget:
            params: dict[str, Any] = {
                "key": api_key,
                "include_games": True,
                "max_results": self._app_list_page_size,
                "last_appid": last_appid,
            }
            try:
                data = await client.get_json(_STORE_APP_LIST_URL, params=params)
            except _UPSTREAM_ERRORS as exc:
                # str(exc) can embed the request URL including the API key — redact.
                log.warning(
                    "app_list_fetch_failed", error=redact_secrets(f"{type(exc).__name__}: {exc}")
                )
                return

            response = data.get("response") or {}
            apps = response.get("apps", [])
            if not apps:
                # No apps past this cursor — a completed pass. Reset so we re-walk.
                ctx.cursor["last_appid"] = 0
                ctx.cursor["last_full_sync"] = datetime.now(UTC).isoformat()
                return

            now = datetime.now(UTC)
            for app in apps:
                if emitted >= budget:
                    break
                appid = app.get("appid")
                name = app.get("name")
                if appid is not None:
                    last_appid = int(appid)
                if appid is None or name is None:
                    # Still advance the cursor so a resume never re-scans this app.
                    ctx.cursor["last_appid"] = last_appid
                    continue
                appid_int = int(appid)
                emitted += 1
                # Advance the checkpoint *before* yielding: if the consumer stops
                # here (ctx.limit reached) the abandoned generator never resumes, so
                # the cursor must already reflect this emitted event.
                ctx.cursor["last_appid"] = last_appid
                yield RawEvent(
                    source=self.name,
                    kind=EventKind.GAME,
                    natural_key=str(appid_int),
                    payload={"name": name},
                    occurred_at=now,
                    platform_app_id=appid_int,
                    fetched_at=now,
                )

            if not response.get("have_more_results"):
                # Reached the end of the catalog within budget — full pass done.
                ctx.cursor["last_appid"] = 0
                ctx.cursor["last_full_sync"] = now.isoformat()
                return
            # API may cap max_results below what we asked; continue from its cursor.
            last_appid = int(response.get("last_appid", last_appid))

    async def _fetch_app_list_keyless(
        self, client: PoliteClient, ctx: FetchContext, already_emitted: int
    ) -> AsyncIterator[RawEvent]:
        """Emit GAME events for a slice of the keyless full-list dump.

        Cursor keys:
          * ``app_list_index`` — resume offset into the (stable-ordered) app list.
          * ``last_full_sync`` — ISO timestamp of the last time we walked to the end.
        """
        try:
            data = await client.get_json(_APP_LIST_URL)
        except _UPSTREAM_ERRORS as exc:
            log.warning(
                "app_list_fetch_failed", error=redact_secrets(f"{type(exc).__name__}: {exc}")
            )
            return

        apps = data.get("applist", {}).get("apps", [])
        # Deterministic order so a resume offset is meaningful across runs.
        apps.sort(key=lambda a: int(a.get("appid", 0)))
        total = len(apps)

        start = int(ctx.cursor.get("app_list_index", 0))
        if start >= total:
            start = 0  # list shrank or we finished a full pass — restart.

        # Remaining budget for this run, respecting ctx.limit and the page size.
        if ctx.limit is not None:
            budget = max(ctx.limit - already_emitted, 0)
        else:
            budget = self._app_list_page_size
        end = min(start + budget, total)

        now = datetime.now(UTC)
        index = start
        for app in apps[start:end]:
            appid = app.get("appid")
            name = app.get("name")
            if appid is None or name is None:
                index += 1
                # Checkpoint even for skipped entries so a resume never re-scans them.
                ctx.cursor["app_list_index"] = index
                continue
            appid_int = int(appid)
            index += 1
            # Advance the checkpoint *before* yielding: if the consumer stops here
            # (ctx.limit reached) the abandoned generator never resumes, so the
            # cursor must already reflect this emitted event.
            ctx.cursor["app_list_index"] = index
            yield RawEvent(
                source=self.name,
                kind=EventKind.GAME,
                natural_key=str(appid_int),
                payload={"name": name},
                occurred_at=now,
                platform_app_id=appid_int,
                fetched_at=now,
            )

        if index >= total:
            ctx.cursor["last_full_sync"] = now.isoformat()

    async def _fetch_most_played(
        self, client: PoliteClient, charted: set[int]
    ) -> AsyncIterator[RawEvent]:
        """Fetch the top-charts once; emit a PLAYER_COUNT sample and a tracking
        GAME event per charted app, and record the charted appids in ``charted``.

        This is what actually *populates* ``tracked`` — the per-app player-count
        phase only polls games already flagged tracked, so without this bootstrap
        nothing would ever be sampled. Degrades (log + return) on upstream failure.
        """
        api_key = get_settings().steam.api_key.get_secret_value()
        params: dict[str, Any] = {}
        if api_key:
            params["key"] = api_key
        try:
            data = await client.get_json(_MOST_PLAYED_URL, params=params)
        except _UPSTREAM_ERRORS as exc:
            # str(exc) can embed the request URL including the API key — redact.
            log.warning(
                "most_played_fetch_failed",
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return

        ranks = (data.get("response") or {}).get("ranks", [])
        now = datetime.now(UTC)
        hour = now.replace(minute=0, second=0, microsecond=0)
        iso_hour = hour.isoformat()
        for entry in ranks:
            appid = entry.get("appid")
            if appid is None:
                continue
            appid_int = int(appid)
            charted.add(appid_int)
            # Mark the charted game tracked. No name in the charts response, so the
            # payload carries only ``tracked`` — the sink must not clobber any
            # existing name (and won't, since we omit the "name" key here).
            yield RawEvent(
                source=self.name,
                kind=EventKind.GAME,
                natural_key=str(appid_int),
                payload={"tracked": True},
                occurred_at=now,
                platform_app_id=appid_int,
                fetched_at=now,
            )
            players = entry.get("concurrent_in_game")
            if players is None:
                continue
            yield RawEvent(
                source=self.name,
                kind=EventKind.PLAYER_COUNT,
                natural_key=f"{appid_int}:{iso_hour}",
                payload={"players": int(players)},
                # Hour-truncated to match the natural key (see per-app phase note).
                occurred_at=hour,
                platform_app_id=appid_int,
                fetched_at=now,
            )

    async def _fetch_player_counts(
        self, client: PoliteClient, *, skip: set[int] | None = None
    ) -> AsyncIterator[RawEvent]:
        """Emit a PLAYER_COUNT sample for each tracked appid (one per hour).

        ``skip`` holds appids already sampled by the top-charts phase this run, so
        we avoid double-polling; tracked games that fell off the charts are still
        covered here.
        """
        skip = skip or set()
        try:
            appids = await self._resolve_appids()
        except Exception as exc:  # provider is external; degrade, don't crash.
            log.warning("appids_provider_failed", error=f"{type(exc).__name__}: {exc}")
            return

        if not appids:
            return

        api_key = get_settings().steam.api_key.get_secret_value()

        for appid in appids:
            if int(appid) in skip:
                continue
            now = datetime.now(UTC)
            hour = now.replace(minute=0, second=0, microsecond=0)
            iso_hour = hour.isoformat()
            params: dict[str, Any] = {"appid": appid}
            if api_key:
                params["key"] = api_key
            try:
                data = await client.get_json(_PLAYER_COUNT_URL, params=params)
            except RetryableStatus as exc:
                # 429/5xx survived all retries — real upstream backpressure. Stop the
                # whole sweep so we don't hammer Steam; remaining appids get sampled
                # next run. str(exc) can embed the API key in the URL — redact.
                log.warning(
                    "player_count_rate_limited",
                    appid=appid,
                    error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                )
                return
            except httpx.HTTPError as exc:
                # A per-app error (timeout, connection reset, a single bad response):
                # skip THIS appid and keep sampling the rest — one bad game must not
                # starve every game after it in the sweep (the core popularity signal).
                log.warning(
                    "player_count_fetch_failed",
                    appid=appid,
                    error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                )
                continue

            response = data.get("response") or {}
            # result == 1 means success; anything else (app has no stats) is skipped.
            if response.get("result") != 1 or "player_count" not in response:
                continue

            yield RawEvent(
                source=self.name,
                kind=EventKind.PLAYER_COUNT,
                natural_key=f"{appid}:{iso_hour}",
                payload={"players": int(response["player_count"])},
                # Hour-truncated to match the natural key: the sink's uq_sample
                # constraint dedups on ts, so re-polling within the same hour
                # must produce the same timestamp to actually be idempotent.
                occurred_at=hour,
                platform_app_id=int(appid),
                fetched_at=now,
            )
