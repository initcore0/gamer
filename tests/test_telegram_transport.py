"""Telegram transport tests — no network.

``Bot.send_message`` is monkeypatched so nothing hits Telegram. We assert:
* success maps to ``ok`` + ``message_id``;
* ``TelegramRetryAfter`` -> retryable, ``TelegramBadRequest`` -> permanent;
* buttons are attached for the DM channel and ignored for the group channel;
* the bot token never appears in a transport's ``repr``.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from gamer.notify import (
    Button,
    Channel,
    Notification,
    TelegramDM,
    TelegramGroup,
    build_inline_keyboard,
)


class _FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.message_id = message_id


def _dm_notification() -> Notification:
    return Notification(
        channel=Channel.TELEGRAM_DM,
        text="Play Hades",
        dedup_key="rec:1",
        buttons=[
            Button(text="👍", action="feedback:up:1"),
            Button(text="👎", action="feedback:down:1"),
        ],
    )


def _make_bot() -> Bot:
    # Token format must be valid for aiogram's constructor, but it's fake.
    return Bot(token="123456:AAFakeTokenForTestsOnly_not_a_real_secret")


# ── build_inline_keyboard ─────────────────────────────────────────────────────


def test_build_inline_keyboard_maps_action_to_callback_data() -> None:
    kb = build_inline_keyboard(_dm_notification().buttons)
    assert kb is not None
    flat = [btn for row in kb.inline_keyboard for btn in row]
    assert [(b.text, b.callback_data) for b in flat] == [
        ("👍", "feedback:up:1"),
        ("👎", "feedback:down:1"),
    ]


def test_build_inline_keyboard_empty_is_none() -> None:
    assert build_inline_keyboard([]) is None


# ── Success path ──────────────────────────────────────────────────────────────


async def test_dm_success_maps_to_ok_and_message_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_send_message(self: Bot, **kwargs: Any) -> _FakeMessage:
        captured.update(kwargs)
        return _FakeMessage(message_id=777)

    monkeypatch.setattr(Bot, "send_message", fake_send_message)

    transport = TelegramDM(bot=_make_bot(), chat_id=42)
    result = await transport.send(_dm_notification())

    assert result.ok is True
    assert result.message_id == "777"
    assert result.retryable is False
    assert captured["chat_id"] == 42
    # DM is interactive: reply_markup carries the buttons.
    assert captured["reply_markup"] is not None


async def test_group_ignores_buttons(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_send_message(self: Bot, **kwargs: Any) -> _FakeMessage:
        captured.update(kwargs)
        return _FakeMessage(message_id=5)

    monkeypatch.setattr(Bot, "send_message", fake_send_message)

    msg = Notification(
        channel=Channel.TELEGRAM_GROUP,
        text="Top movers today",
        dedup_key="digest:2026-07-09",
        buttons=[Button(text="👍", action="feedback:up:1")],
    )
    transport = TelegramGroup(bot=_make_bot(), chat_id=99)
    result = await transport.send(msg)

    assert result.ok is True
    assert captured["chat_id"] == 99
    # Group is read-only: buttons dropped.
    assert captured["reply_markup"] is None


# ── Failure mapping ───────────────────────────────────────────────────────────


async def test_retry_after_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_retry(self: Bot, **kwargs: Any) -> None:
        raise TelegramRetryAfter(
            method=None,  # type: ignore[arg-type]
            message="Too Many Requests",
            retry_after=7,
        )

    monkeypatch.setattr(Bot, "send_message", raise_retry)

    transport = TelegramDM(bot=_make_bot(), chat_id=42)
    result = await transport.send(_dm_notification())

    assert result.ok is False
    assert result.retryable is True
    assert result.message_id is None


async def test_bad_request_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def raise_bad(self: Bot, **kwargs: Any) -> None:
        raise TelegramBadRequest(
            method=None,  # type: ignore[arg-type]
            message="chat not found",
        )

    monkeypatch.setattr(Bot, "send_message", raise_bad)

    transport = TelegramDM(bot=_make_bot(), chat_id=42)
    result = await transport.send(_dm_notification())

    assert result.ok is False
    assert result.retryable is False
    assert result.error == "chat not found"


# ── Token hygiene ─────────────────────────────────────────────────────────────


def test_token_not_in_transport_repr() -> None:
    token = "123456:AAFakeTokenForTestsOnly_not_a_real_secret"
    transport = TelegramDM(bot=Bot(token=token), chat_id=42)
    assert token not in repr(transport)
