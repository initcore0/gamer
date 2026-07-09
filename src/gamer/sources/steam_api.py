"""Steam Web API source adapter (official endpoints only — no scraping).

Covers two official Steam Web API endpoints:

* ``ISteamApps/GetAppList/v2`` — the full app catalog. Each appid is emitted as
  an :class:`EventKind.GAME` event (natural key = the appid). The list is huge and
  mostly static, so we checkpoint our position in ``ctx.cursor`` and resume across
  runs, honouring ``ctx.limit`` as a soft per-run cap.
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
_APP_LIST_URL = f"{_BASE}/ISteamApps/GetAppList/v2/"
_PLAYER_COUNT_URL = f"{_BASE}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"

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
        """Yield GAME events (catalog sync) then PLAYER_COUNT events (tracked games).

        Honours ``ctx.limit`` and mutates ``ctx.cursor`` in place so a huge catalog
        resumes across runs. Never raises on expected upstream failures.
        """
        emitted = 0
        async with self._client() as client:
            async for event in self._fetch_app_list(client, ctx, emitted):
                emitted += 1
                yield event
                if ctx.limit is not None and emitted >= ctx.limit:
                    return

            async for event in self._fetch_player_counts(client):
                emitted += 1
                yield event
                if ctx.limit is not None and emitted >= ctx.limit:
                    return

    async def _fetch_app_list(
        self, client: PoliteClient, ctx: FetchContext, already_emitted: int
    ) -> AsyncIterator[RawEvent]:
        """Emit GAME events for a slice of the catalog, checkpointing progress.

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

    async def _fetch_player_counts(self, client: PoliteClient) -> AsyncIterator[RawEvent]:
        """Emit a PLAYER_COUNT sample for each tracked appid (one per hour)."""
        try:
            appids = await self._resolve_appids()
        except Exception as exc:  # provider is external; degrade, don't crash.
            log.warning("appids_provider_failed", error=f"{type(exc).__name__}: {exc}")
            return

        if not appids:
            return

        api_key = get_settings().steam.api_key.get_secret_value()

        for appid in appids:
            now = datetime.now(UTC)
            hour = now.replace(minute=0, second=0, microsecond=0)
            iso_hour = hour.isoformat()
            params: dict[str, Any] = {"appid": appid}
            if api_key:
                params["key"] = api_key
            try:
                data = await client.get_json(_PLAYER_COUNT_URL, params=params)
            except _UPSTREAM_ERRORS as exc:
                # 429/5xx (after retries) or timeout — log and stop, per contract.
                # str(exc) can embed the request URL including the API key — redact.
                log.warning(
                    "player_count_fetch_failed",
                    appid=appid,
                    error=redact_secrets(f"{type(exc).__name__}: {exc}"),
                )
                return

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
