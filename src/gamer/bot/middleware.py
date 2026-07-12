"""Outer allowlist middleware for the bot router (multi-user).

When ``GAMER_TELEGRAM__ALLOWED_CHAT_IDS`` is non-empty, this aiogram outer
middleware refuses messages *and* callback queries from any chat not on the list,
before any handler runs. An empty allowlist (the default) is fully open — the
middleware is still installed but every chat passes, so wiring it unconditionally
is harmless.

The pure decision (:func:`~gamer.bot.keys.is_chat_allowed`) is unit-tested on its
own; this module is the thin aiogram adapter that reads the chat id off the event
and, on refusal, answers politely instead of silently dropping (a dropped
callback leaves the client spinning).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from gamer.bot.keys import is_chat_allowed
from gamer.logging import get_logger

log = get_logger("bot.middleware")

_REFUSAL = "Sorry — this bot is private and your chat isn't on its allowlist."


class AllowlistMiddleware(BaseMiddleware):
    """Refuse events from chats outside the configured allowlist."""

    def __init__(self, allowed: frozenset[int]) -> None:
        self._allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        chat_id = _event_chat_id(event)
        if is_chat_allowed(chat_id, self._allowed):
            return await handler(event, data)

        log.info("chat_refused", chat_id=chat_id)
        # Answer politely so the user isn't left guessing / the client stops
        # spinning; then stop propagation by not calling ``handler``.
        await _refuse(event)
        return None


def _event_chat_id(event: TelegramObject) -> int | None:
    """The chat id off a Message or a CallbackQuery's message, else ``None``."""
    if isinstance(event, Message):
        return event.chat.id
    if isinstance(event, CallbackQuery):
        message = event.message
        return message.chat.id if message is not None else None
    return None


async def _refuse(event: TelegramObject) -> None:
    """Send the polite refusal — a plain reply for a Message, an alert for a
    CallbackQuery (so the tap resolves rather than spinning)."""
    if isinstance(event, CallbackQuery):
        await event.answer(_REFUSAL, show_alert=True)
    elif isinstance(event, Message):
        await event.answer(_REFUSAL)
