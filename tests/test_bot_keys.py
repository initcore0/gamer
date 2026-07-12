"""Unit tests for the multi-user key derivation + allowlist decision (pure)."""

from __future__ import annotations

from dataclasses import dataclass

from gamer.bot.keys import (
    is_chat_allowed,
    pref_key_for_chat_id,
    pref_key_from_event,
)


@dataclass
class _Chat:
    id: int


@dataclass
class _Message:
    chat: _Chat


@dataclass
class _Callback:
    message: _Message | None


def test_pref_key_for_chat_id_dm_positive() -> None:
    # DM chat ids are positive (== user id).
    assert pref_key_for_chat_id(123456789) == "123456789"


def test_pref_key_for_chat_id_group_negative() -> None:
    # Group/supergroup ids are negative.
    assert pref_key_for_chat_id(-1001234567890) == "-1001234567890"


def test_pref_key_from_message() -> None:
    assert pref_key_from_event(_Message(chat=_Chat(id=42))) == "42"


def test_pref_key_from_callback_reads_nested_chat() -> None:
    cb = _Callback(message=_Message(chat=_Chat(id=-99)))
    assert pref_key_from_event(cb) == "-99"


def test_pref_key_from_event_none_when_no_chat() -> None:
    # A callback whose message is inaccessible (None) yields no key.
    assert pref_key_from_event(_Callback(message=None)) is None
    assert pref_key_from_event(object()) is None


def test_is_chat_allowed_empty_allowlist_is_open() -> None:
    # Empty allowlist => everyone (and even an unknown chat) passes.
    assert is_chat_allowed(123, frozenset()) is True
    assert is_chat_allowed(None, frozenset()) is True


def test_is_chat_allowed_enforced_when_configured() -> None:
    allowed = frozenset({111, -222})
    assert is_chat_allowed(111, allowed) is True
    assert is_chat_allowed(-222, allowed) is True
    assert is_chat_allowed(999, allowed) is False
    # An unidentifiable chat is refused whenever the gate is active.
    assert is_chat_allowed(None, allowed) is False
