"""Unit tests for pagination cursor + ILIKE-escape helpers (UI_PLAN.md §4, §5).

Pure functions — no DB, no network. Cursors must round-trip, and any
tampered/garbage token must degrade to ``None`` (first page), never raise.
"""

from __future__ import annotations

import base64

import pytest

from gamer.api.deps import decode_cursor, encode_cursor, escape_like


def test_cursor_round_trip_str_int() -> None:
    token = encode_cursor(("Hollow Knight", 42))
    assert decode_cursor(token, (str, int)) == ("Hollow Knight", 42)


def test_cursor_round_trip_preserves_unicode() -> None:
    token = encode_cursor(("Ōkami 大神", 7))
    assert decode_cursor(token, (str, int)) == ("Ōkami 大神", 7)


def test_cursor_is_url_safe() -> None:
    token = encode_cursor(("a/b+c=d", 1))
    # base64url alphabet only (plus optional '=' padding) — safe in a query string.
    assert all(c.isalnum() or c in "-_=" for c in token)


@pytest.mark.parametrize("token", [None, ""])
def test_missing_token_is_first_page(token: str | None) -> None:
    assert decode_cursor(token, (str, int)) is None


@pytest.mark.parametrize(
    "token",
    [
        "not-base64-!!!",
        "@@@@",
        base64.urlsafe_b64encode(b"not json").decode(),
        base64.urlsafe_b64encode(b'{"a": 1}').decode(),  # object, not list
        base64.urlsafe_b64encode(b"[1, 2, 3]").decode(),  # wrong arity
    ],
)
def test_garbage_token_returns_none(token: str) -> None:
    assert decode_cursor(token, (str, int)) is None


def test_wrong_element_type_returns_none() -> None:
    token = encode_cursor((42, "oops"))  # (int, str) but expecting (str, int)
    assert decode_cursor(token, (str, int)) is None


def test_bool_rejected_for_int_slot() -> None:
    # JSON true decodes to bool, a subclass of int — must not pass an int slot.
    token = base64.urlsafe_b64encode(b'["x", true]').decode()
    assert decode_cursor(token, (str, int)) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", "plain"),
        ("50%", "50\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("%_\\", "\\%\\_\\\\"),
    ],
)
def test_escape_like(raw: str, expected: str) -> None:
    assert escape_like(raw) == expected
