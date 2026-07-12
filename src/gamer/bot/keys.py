"""Preference-key derivation + the optional chat allowlist (multi-user).

The bot is multi-user: every Telegram chat gets its own preference profile keyed
by ``pref_key = str(chat_id)``. In Telegram, a DM chat's id is positive and equal
to the user's id; a group/supergroup chat's id is negative. ``"default"`` is the
legacy/global profile that predates multi-user.

Both functions here are **pure** (they read only the ids off a message/callback,
no DB, no network) so the derivation and the allowlist decision are unit-tested
directly. The aiogram wiring (the outer middleware) lives in ``bot.middleware``.
"""

from __future__ import annotations

from typing import Protocol


class _HasChatId(Protocol):
    """Structural view of the ``.chat.id`` we need — an aiogram ``Message`` or a
    ``CallbackQuery.message`` both satisfy it, and a test stub can too."""

    @property
    def chat(self) -> object: ...


def pref_key_for_chat_id(chat_id: int) -> str:
    """The preference key for a Telegram chat id — just its string form.

    DM chat ids are positive (== the user id); group/supergroup ids are negative.
    Both map straight to ``str(chat_id)``; ``"default"`` is reserved for the
    legacy profile and is never produced here.
    """
    return str(chat_id)


def pref_key_from_event(event: object) -> str | None:
    """Derive ``pref_key`` from an aiogram ``Message`` or ``CallbackQuery``.

    Reads ``event.chat.id`` for a Message, or ``event.message.chat.id`` for a
    CallbackQuery. Returns ``None`` when neither is present (e.g. an inline query
    or an inaccessible callback message) so callers can bail gracefully rather
    than mutate the wrong profile.
    """
    chat_id = _chat_id_from_event(event)
    return None if chat_id is None else pref_key_for_chat_id(chat_id)


def _chat_id_from_event(event: object) -> int | None:
    """Best-effort ``chat.id`` off a Message or CallbackQuery, else ``None``.

    Guards on attribute presence (not concrete type) so a lightweight test stub
    with ``.chat.id`` — or ``.message.chat.id`` — works without importing aiogram.
    """
    chat = getattr(event, "chat", None)
    if chat is None:
        message = getattr(event, "message", None)
        chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    return chat_id if isinstance(chat_id, int) else None


def is_chat_allowed(chat_id: int | None, allowed: frozenset[int]) -> bool:
    """The allowlist decision — pure, so it's unit-tested without aiogram.

    An empty ``allowed`` set means *open to everyone* (the default posture): every
    chat is allowed. When non-empty, only listed chat ids pass. A ``None`` chat id
    (couldn't determine the chat) is refused whenever the allowlist is active — we
    never let an unidentifiable chat through a configured gate.
    """
    if not allowed:
        return True
    return chat_id is not None and chat_id in allowed
