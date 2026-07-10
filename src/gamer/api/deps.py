"""Shared query-param helpers for the web UI (UI_PLAN.md §4, §5).

Keyset ("seek") pagination is the plan's rule: every list cursor is a
``(sort_value, id)`` tuple, encoded into an *opaque* token — never a raw
``OFFSET`` (page 400 must cost the same as page 1). The token is JSON encoded
then base64url'd; it carries no secrets (only public sort values already shown
in the list) but is validated defensively on decode so a tampered/garbage token
degrades to "first page" (``None``) rather than a 500.

These are pure functions — the unit-test surface for pagination — and must stay
DB-free and import-cheap.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

# ILIKE special characters that must be escaped so user input is treated as a
# literal, not a pattern. Backslash first so we don't double-escape.
_LIKE_SPECIALS = ("\\", "%", "_")


def escape_like(value: str) -> str:
    """Escape ILIKE wildcards (``%``, ``_``, ``\\``) in user-supplied search text.

    The result is meant to be wrapped as ``f"%{escaped}%"`` and passed to
    ``ILIKE ... ESCAPE '\\'`` so ``%`` / ``_`` in the query match literally.
    """
    out = value
    for ch in _LIKE_SPECIALS:
        out = out.replace(ch, "\\" + ch)
    return out


def encode_cursor(values: tuple[Any, ...]) -> str:
    """Encode a keyset tuple into an opaque, URL-safe token.

    The tuple is the ordering key of the last row on the page (e.g.
    ``(name, id)``). Values must be JSON-serializable scalars.
    """
    raw = json.dumps(list(values), separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(
    token: str | None,
    expected_types: tuple[type, ...],
) -> tuple[Any, ...] | None:
    """Decode a token back into a keyset tuple, or ``None`` if it is unusable.

    Returns ``None`` — meaning "start from the first page" — for a missing,
    malformed, or type-mismatched token, so a hand-edited cursor never 500s.
    ``expected_types`` pins the arity and element types of the tuple (e.g.
    ``(str, int)`` for ``(name, id)``); ``bool`` is rejected where ``int`` is
    expected since JSON ``true``/``false`` decode to ``bool``.
    """
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        parsed = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, list) or len(parsed) != len(expected_types):
        return None
    for value, expected in zip(parsed, expected_types, strict=True):
        # bool is a subclass of int — reject it explicitly for int columns.
        if expected is int and isinstance(value, bool):
            return None
        if not isinstance(value, expected):
            return None
    return tuple(parsed)
