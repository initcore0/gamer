"""Tests for the allowlist outer middleware (multi-user).

The pure decision lives in ``gamer.bot.keys.is_chat_allowed`` (tested in
``test_bot_keys``), and the chat-id extraction / refusal reply are exercised
against real aiogram model types. Here we cover the middleware's *control flow*:
allowed chats call the handler; disallowed chats are refused (handler skipped,
polite reply sent). ``_event_chat_id`` and ``_refuse`` are patched so the flow is
observable without a live Telegram bot.
"""

from __future__ import annotations

import gamer.bot.middleware as mw_mod
from gamer.bot.middleware import AllowlistMiddleware


async def _passthrough(event: object, data: dict[str, object]) -> str:
    return "handled"


async def test_open_allowlist_passes_everyone() -> None:
    mw = AllowlistMiddleware(frozenset())
    result = await mw(_passthrough, object(), {})  # type: ignore[arg-type]
    assert result == "handled"


async def test_allowed_chat_calls_handler(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(mw_mod, "_event_chat_id", lambda e: 111)
    refused: list[object] = []

    async def _refuse(event: object) -> None:
        refused.append(event)

    monkeypatch.setattr(mw_mod, "_refuse", _refuse)

    mw = AllowlistMiddleware(frozenset({111}))
    result = await mw(_passthrough, object(), {})  # type: ignore[arg-type]
    assert result == "handled"
    assert refused == []  # allowed → never refused


async def test_disallowed_chat_is_refused_not_handled(
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(mw_mod, "_event_chat_id", lambda e: 999)
    refused: list[object] = []

    async def _refuse(event: object) -> None:
        refused.append(event)

    monkeypatch.setattr(mw_mod, "_refuse", _refuse)

    handled = False

    async def _handler(event: object, data: dict[str, object]) -> str:
        nonlocal handled
        handled = True
        return "handled"

    mw = AllowlistMiddleware(frozenset({111}))
    sentinel = object()
    result = await mw(_handler, sentinel, {})  # type: ignore[arg-type]
    assert result is None  # handler not called
    assert handled is False
    assert refused == [sentinel]  # refusal delivered for this exact event
