"""Discord webhook transport tests — no live network.

The webhook is respx-mocked so nothing hits Discord. We assert:
* 204 → ok (no message id); 429 → retryable; 400 → permanent;
* the pure HTML→Discord-markdown conversion (bold/italic/link/entities);
* content is truncated at Discord's 2000-char limit;
* an unconfigured webhook makes the factory return ``None``;
* the webhook URL never leaks into a log event or the transport's ``repr``.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from gamer.config import Settings
from gamer.notify import (
    Channel,
    DiscordWebhook,
    Notification,
    build_discord_transport,
    html_to_discord_markdown,
)
from gamer.notify.discord import DISCORD_CONTENT_LIMIT
from gamer.sources.http import PoliteClient

_WEBHOOK = "https://discord.com/api/webhooks/123456789/tok-en_SECRET_value"


def _digest() -> Notification:
    return Notification(
        channel=Channel.DISCORD,
        text='<b>🎮 Top movers</b>\n\n1. <a href="https://ex/1">Hades &amp; Co</a> <i>hot</i>',
        dedup_key="digest:discord:2026-07-09",
    )


# ── Pure html_to_discord_markdown ─────────────────────────────────────────────


def test_bold_converts_to_double_star() -> None:
    assert html_to_discord_markdown("<b>hi</b>") == "**hi**"


def test_italic_converts_to_single_star() -> None:
    assert html_to_discord_markdown("<i>hi</i>") == "*hi*"


def test_link_converts_to_markdown() -> None:
    assert html_to_discord_markdown('<a href="https://ex/1">Play</a>') == "[Play](https://ex/1)"


def test_entities_are_unescaped() -> None:
    assert html_to_discord_markdown("A &amp; B &lt;c&gt;") == "A & B <c>"


def test_link_with_entity_in_label() -> None:
    assert (
        html_to_discord_markdown('<a href="https://ex">Hades &amp; Co</a>')
        == "[Hades & Co](https://ex)"
    )


def test_mixed_markup_full_digest_line() -> None:
    out = html_to_discord_markdown(_digest().text)
    assert out == "**🎮 Top movers**\n\n1. [Hades & Co](https://ex/1) *hot*"


def test_unknown_tags_are_stripped() -> None:
    assert html_to_discord_markdown("<span>x</span>") == "x"


# ── Truncation ────────────────────────────────────────────────────────────────


@respx.mock
async def test_content_truncated_at_limit() -> None:
    route = respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
    long = Notification(channel=Channel.DISCORD, text="x" * 5000, dedup_key="d:1")
    async with DiscordWebhook(webhook_url=_WEBHOOK) as tp:
        result = await tp.send(long)
    assert result.ok is True
    sent = route.calls.last.request
    body = sent.read().decode()
    content = json.loads(body)["content"]
    assert len(content) <= DISCORD_CONTENT_LIMIT
    assert content.endswith("…")


# ── Status mapping ────────────────────────────────────────────────────────────


@respx.mock
async def test_204_is_ok_no_message_id() -> None:
    respx.post(_WEBHOOK).mock(return_value=httpx.Response(204))
    async with DiscordWebhook(webhook_url=_WEBHOOK) as tp:
        result = await tp.send(_digest())
    assert result.ok is True
    assert result.message_id is None
    assert result.retryable is False


@respx.mock
async def test_wait_true_success_captures_message_id() -> None:
    respx.post(_WEBHOOK).mock(return_value=httpx.Response(200, json={"id": "999", "content": "x"}))
    async with DiscordWebhook(webhook_url=_WEBHOOK) as tp:
        result = await tp.send(_digest())
    assert result.ok is True
    assert result.message_id == "999"


@respx.mock
async def test_429_is_retryable() -> None:
    # PoliteClient retries 429 internally; after exhausting attempts it re-raises
    # RetryableStatus, which the transport maps to a retryable failure.
    respx.post(_WEBHOOK).mock(return_value=httpx.Response(429, json={"retry_after": 0.01}))
    # Small max_attempts keeps the test fast.
    async with DiscordWebhook(webhook_url=_WEBHOOK, client=_fast_client()) as tp:
        result = await tp.send(_digest())
    assert result.ok is False
    assert result.retryable is True


@respx.mock
async def test_400_is_not_retryable() -> None:
    respx.post(_WEBHOOK).mock(return_value=httpx.Response(400, json={"message": "bad"}))
    async with DiscordWebhook(webhook_url=_WEBHOOK) as tp:
        result = await tp.send(_digest())
    assert result.ok is False
    assert result.retryable is False
    assert result.error == "http 400"


@respx.mock
async def test_transport_error_is_retryable() -> None:
    respx.post(_WEBHOOK).mock(side_effect=httpx.ConnectError("boom"))
    async with DiscordWebhook(webhook_url=_WEBHOOK, client=_fast_client()) as tp:
        result = await tp.send(_digest())
    assert result.ok is False
    assert result.retryable is True


def _fast_client() -> PoliteClient:
    return PoliteClient(rate=100, per=1.0, max_attempts=2)


# ── Factory ───────────────────────────────────────────────────────────────────


def test_build_returns_none_when_disabled() -> None:
    settings = Settings()  # no GAMER_DISCORD__WEBHOOK_URL → disabled
    assert settings.discord.enabled is False
    assert build_discord_transport(settings) is None


def test_build_returns_transport_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAMER_DISCORD__WEBHOOK_URL", _WEBHOOK)
    settings = Settings()
    tp = build_discord_transport(settings)
    assert isinstance(tp, DiscordWebhook)
    assert tp is not None


# ── URL hygiene ───────────────────────────────────────────────────────────────


def test_url_not_in_repr() -> None:
    tp = DiscordWebhook(webhook_url=_WEBHOOK)
    assert _WEBHOOK not in repr(tp)
    assert "SECRET" not in repr(tp)


@respx.mock
async def test_url_not_in_logs_on_error(capfd: pytest.CaptureFixture[str]) -> None:
    from gamer.logging import configure_logging

    configure_logging(level="INFO", json=False)
    respx.post(_WEBHOOK).mock(return_value=httpx.Response(400, json={"message": "bad"}))
    async with DiscordWebhook(webhook_url=_WEBHOOK) as tp:
        await tp.send(_digest())
    out, err = capfd.readouterr()
    combined = out + err
    assert _WEBHOOK not in combined
    assert "tok-en_SECRET_value" not in combined
