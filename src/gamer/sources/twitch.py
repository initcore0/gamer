"""Twitch Helix source adapter (PLAN.md §3 — "Streaming meta").

Emits current Twitch viewer counts per game as :class:`EventKind.TWITCH` events,
which the sink persists as ``TWITCH_VIEWERS`` signal samples. That series feeds
the watchability score component (viewers-to-players ratio).

Flow:

1. Obtain an **app access token** via the OAuth *client-credentials* grant
   (``POST https://id.twitch.tv/oauth2/token``) using the configured client id /
   secret. The token is cached in-process until shortly before it expires so we
   fetch it once and reuse it across polls.
2. ``GET helix/games/top`` — the games with the most current viewers, each with a
   Twitch ``game_id`` and name.
3. ``GET helix/streams?game_id=`` per top game — sum ``viewer_count`` across live
   streams to get that game's current total viewers.

Each game becomes a TWITCH event with payload
``{"viewers": int, "twitch_game_id": str, "game_name": str}`` and natural key
``"{twitch_game_id}:{iso_hour}"`` (hour-truncated so at most one sample per game
per hour survives the idempotent sink). When a Twitch game name maps to a known
Steam appid, ``platform_app_id`` is set so the sample links to the right game;
otherwise it is left ``None`` and the name travels in the payload.

The adapter no-ops gracefully when Twitch is not configured
(``settings.twitch.enabled`` is False). All HTTP goes through
:class:`PoliteClient`; ``fetch`` never raises on expected upstream failures
(401/429/5xx, network errors) — it logs (redacting any credential-bearing URL)
and stops, per the :class:`Source` contract.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from gamer.config import get_settings
from gamer.db import session_scope
from gamer.logging import get_logger, redact_secrets
from gamer.sources.base import EventKind, FetchContext, RawEvent
from gamer.sources.http import PoliteClient, RetryableStatus

log = get_logger("sources.twitch")

# Expected upstream failures that must degrade (log + stop) rather than crash the
# run: httpx transport/status errors plus PoliteClient's retry-exhausted signal.
_UPSTREAM_ERRORS = (httpx.HTTPError, RetryableStatus)

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_HELIX_BASE = "https://api.twitch.tv/helix"
_TOP_GAMES_URL = f"{_HELIX_BASE}/games/top"
_STREAMS_URL = f"{_HELIX_BASE}/streams"

#: Refresh the token this many seconds before its stated expiry (clock skew /
#: in-flight requests).
_TOKEN_EXPIRY_SKEW_SECONDS = 60.0

#: Resolves a Twitch game name to a Steam appid, or None. May be sync or async.
AppidResolver = Callable[[str], int | None | Awaitable[int | None]]


async def _steam_appid_by_name(name: str) -> int | None:
    """Default resolver: best-effort case-insensitive name → Steam appid.

    Returns ``None`` on any lookup failure so the source degrades to name-only
    (the sink still records the sample keyed by twitch_game_id).
    """
    from sqlalchemy import func, select

    from gamer.db.models import Game, Platform

    try:
        async with session_scope() as session:
            appid = (
                await session.execute(
                    select(Game.platform_app_id)
                    .where(Game.platform == Platform.STEAM)
                    .where(func.lower(Game.name) == name.lower())
                    .limit(1)
                )
            ).scalar_one_or_none()
        return None if appid is None else int(appid)
    except Exception as exc:  # DB is external to this adapter; degrade, don't crash.
        log.debug("appid_resolve_failed", name=name, error=f"{type(exc).__name__}: {exc}")
        return None


class TwitchSource:
    """Twitch Helix adapter. Implements the :class:`Source` protocol."""

    name = "twitch"
    default_interval_seconds = 3600

    def __init__(
        self,
        *,
        appid_resolver: AppidResolver | None = None,
        top_games_limit: int = 20,
        rate: int = 30,
        per: float = 60.0,
        max_attempts: int = 4,
    ) -> None:
        # Injectable so unit tests need no DB; defaults to a name→appid query.
        self._appid_resolver: AppidResolver = appid_resolver or _steam_appid_by_name
        self._top_games_limit = top_games_limit
        self._rate = rate
        self._per = per
        self._max_attempts = max_attempts
        # In-process app-access-token cache: (token, monotonic expiry).
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def _client(self, *, headers: dict[str, str] | None = None) -> PoliteClient:
        return PoliteClient(
            rate=self._rate,
            per=self._per,
            max_attempts=self._max_attempts,
            headers=headers,
        )

    async def _resolve_appid(self, name: str) -> int | None:
        resolved = self._appid_resolver(name)
        if isinstance(resolved, Awaitable):
            return await resolved
        return resolved

    async def _get_token(
        self, client: PoliteClient, client_id: str, client_secret: str
    ) -> str | None:
        """Return a cached app access token, fetching a fresh one near expiry.

        Uses the OAuth client-credentials grant. Returns ``None`` on failure so
        the caller can degrade (emit nothing) without raising.
        """
        now = time.monotonic()
        if self._token is not None and now < self._token_expiry:
            return self._token

        try:
            resp = await client.request(
                "POST",
                _TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials",
                },
            )
            resp.raise_for_status()
            body = resp.json()
        except (*_UPSTREAM_ERRORS, ValueError) as exc:
            # str(exc) / the URL never carries the secret (it's form-encoded in the
            # body), but redact defensively before logging.
            log.warning("token_fetch_failed", error=redact_secrets(f"{type(exc).__name__}: {exc}"))
            return None

        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            log.warning("token_missing_in_response")
            return None
        expires_in = float(body.get("expires_in", 0) or 0)
        self._token = token
        self._token_expiry = now + max(0.0, expires_in - _TOKEN_EXPIRY_SKEW_SECONDS)
        log.info("token_acquired", expires_in=int(expires_in))
        return token

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        """Yield TWITCH viewer-count events for the current top games.

        No-ops (emits nothing) when Twitch is not configured. Honours ``ctx.limit``
        and never raises on expected upstream failures.
        """
        settings = get_settings().twitch
        if not settings.enabled:
            log.info("twitch_disabled_no_credentials")
            return

        client_id = settings.client_id.get_secret_value()
        client_secret = settings.client_secret.get_secret_value()

        async with self._client() as auth_client:
            token = await self._get_token(auth_client, client_id, client_secret)
        if token is None:
            return

        headers = {"Client-Id": client_id, "Authorization": f"Bearer {token}"}
        emitted = 0
        async with self._client(headers=headers) as client:
            games = await self._fetch_top_games(client, ctx)
            for game in games:
                event = await self._build_event(client, game)
                if event is None:
                    continue
                emitted += 1
                yield event
                if ctx.limit is not None and emitted >= ctx.limit:
                    return

    async def _fetch_top_games(
        self, client: PoliteClient, ctx: FetchContext
    ) -> Sequence[dict[str, Any]]:
        """Return the current most-watched games (each has ``id`` and ``name``)."""
        first = self._top_games_limit
        if ctx.limit is not None:
            first = min(first, ctx.limit)
        try:
            data = await client.get_json(_TOP_GAMES_URL, params={"first": first})
        except _UPSTREAM_ERRORS as exc:
            log.warning(
                "top_games_fetch_failed",
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return []
        games = data.get("data") or []
        return [g for g in games if isinstance(g, dict) and g.get("id")]

    async def _build_event(self, client: PoliteClient, game: dict[str, Any]) -> RawEvent | None:
        """Sum live-stream viewers for one game → a TWITCH RawEvent (or None)."""
        twitch_game_id = str(game.get("id"))
        game_name = str(game.get("name") or "")

        try:
            data = await client.get_json(
                _STREAMS_URL, params={"game_id": twitch_game_id, "first": 100}
            )
        except _UPSTREAM_ERRORS as exc:
            log.warning(
                "streams_fetch_failed",
                twitch_game_id=twitch_game_id,
                error=redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            return None

        streams = data.get("data") or []
        viewers = sum(int(s.get("viewer_count", 0) or 0) for s in streams if isinstance(s, dict))

        now = datetime.now(UTC)
        hour = now.replace(minute=0, second=0, microsecond=0)
        iso_hour = hour.isoformat()

        app_id = await self._resolve_appid(game_name) if game_name else None

        payload: dict[str, Any] = {
            "viewers": viewers,
            "twitch_game_id": twitch_game_id,
            "game_name": game_name,
        }
        return RawEvent(
            source=self.name,
            kind=EventKind.TWITCH,
            natural_key=f"{twitch_game_id}:{iso_hour}",
            payload=payload,
            # Hour-truncated to match the natural key so re-polling within the same
            # hour produces the same ts and the sink dedups it (uq_sample).
            occurred_at=hour,
            platform_app_id=app_id,
            fetched_at=now,
        )
