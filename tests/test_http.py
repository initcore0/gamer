from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from gamer.sources.http import PoliteClient, RateLimiter


async def test_rate_limiter_throttles() -> None:
    # 5 tokens/sec: the 6th acquire in a burst must wait.
    limiter = RateLimiter(rate=5, per=1.0)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(6):
        await limiter.acquire()
    elapsed = loop.time() - start
    assert elapsed >= 0.15  # forced to wait for a token to refill


async def test_rate_limiter_sustained_rate_is_not_doubled() -> None:
    """Sustained demand must honour the configured rate, not ~2x it.

    Regression: after sleeping to earn a token the limiter left ``_last`` at the
    pre-sleep instant, so the next acquire re-credited the slept interval —
    doubling throughput. With rate=10/sec, 20 back-to-back acquires must earn 10
    tokens beyond the initial full bucket, i.e. take >= ~1.0s. The old double-
    credit code finished in roughly half that.
    """
    limiter = RateLimiter(rate=10, per=1.0)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(20):
        await limiter.acquire()
    elapsed = loop.time() - start
    # 10 tokens must be earned at 10/sec => >= ~1.0s; allow slack for scheduling.
    assert elapsed >= 0.85, f"sustained rate too fast ({elapsed:.2f}s) — double-credit?"


@respx.mock
async def test_get_json_success() -> None:
    respx.get("https://api.example/x").mock(return_value=httpx.Response(200, json={"ok": True}))
    async with PoliteClient(rate=100, per=1.0) as client:
        data = await client.get_json("https://api.example/x")
    assert data == {"ok": True}


@respx.mock
async def test_retries_on_500_then_succeeds() -> None:
    route = respx.get("https://api.example/flaky")
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json={"ok": 1}),
    ]
    async with PoliteClient(rate=100, per=1.0, max_attempts=3) as client:
        data = await client.get_json("https://api.example/flaky")
    assert data == {"ok": 1}
    assert route.call_count == 2


@respx.mock
async def test_4xx_is_not_retried() -> None:
    route = respx.get("https://api.example/nope").mock(return_value=httpx.Response(404))
    async with PoliteClient(rate=100, per=1.0, max_attempts=3) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_json("https://api.example/nope")
    assert route.call_count == 1
