"""Polite HTTP client shared by sources.

Provides an ``httpx.AsyncClient`` wrapper with:
  * a per-client token-bucket rate limiter (politeness toward free APIs),
  * tenacity retry/backoff on transient errors and 429/5xx,
  * respect for ``Retry-After``.

Source adapters construct one :class:`PoliteClient` with their own rate limit.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Self

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from gamer.logging import get_logger

log = get_logger("sources.http")


class RateLimiter:
    """Simple async token bucket. ``rate`` tokens per ``per`` seconds."""

    def __init__(self, rate: int, per: float = 1.0) -> None:
        self._rate = rate
        self._per = per
        self._allowance = float(rate)
        # lazily initialized on first acquire to avoid reading the clock at import.
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._last is None:
                self._last = now
            elapsed = now - self._last
            self._last = now
            self._allowance = min(
                float(self._rate), self._allowance + elapsed * (self._rate / self._per)
            )
            if self._allowance < 1.0:
                sleep_for = (1.0 - self._allowance) * (self._per / self._rate)
                await asyncio.sleep(sleep_for)
                # We slept exactly long enough to earn one token, then spend it.
                # Advance _last past the slept interval too, otherwise the next
                # acquire's `elapsed` would re-credit these same tokens a second
                # time — doubling the effective rate (429s against polite APIs).
                self._last = loop.time()
                self._allowance = 0.0
            else:
                self._allowance -= 1.0


class RetryableStatus(Exception):
    """Raised for 429/5xx so tenacity retries with backoff."""


class PoliteClient:
    """Rate-limited, retrying async HTTP client.

    Use as an async context manager::

        async with PoliteClient(rate=40, per=60) as client:
            data = await client.get_json(url)
    """

    def __init__(
        self,
        *,
        rate: int,
        per: float = 1.0,
        timeout: float = 20.0,
        headers: dict[str, str] | None = None,
        max_attempts: int = 4,
    ) -> None:
        self._limiter = RateLimiter(rate, per)
        self._max_attempts = max_attempts
        default_headers = {"User-Agent": "gamer/0.1 (+https://github.com/initcore0/gamer)"}
        if headers:
            default_headers.update(headers)
        self._client = httpx.AsyncClient(timeout=timeout, headers=default_headers)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Rate-limited, retried request. Raises on non-retryable 4xx."""

        @retry(
            retry=retry_if_exception_type((RetryableStatus, httpx.TransportError)),
            wait=wait_exponential_jitter(initial=1, max=30),
            stop=stop_after_attempt(self._max_attempts),
            reraise=True,
        )
        async def _do() -> httpx.Response:
            await self._limiter.acquire()
            resp = await self._client.request(method, url, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        await asyncio.sleep(min(float(retry_after), 60.0))
                    except ValueError:
                        pass
                log.warning("retryable_status", url=url, status=resp.status_code)
                raise RetryableStatus(f"{resp.status_code} for {url}")
            return resp

        return await _do()

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        resp = await self.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()
