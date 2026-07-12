"""Preference-profile query layer (API_CONTRACT.md — user switcher).

``/api/v1/users`` lists one entry per ``streamer_prefs`` row for the React
user-switcher: the profile key, its display label, taste/subscription genre
sets, how many games it has muted, digest state, and when it was created.

The legacy ``"default"`` profile stores ``label = NULL`` (it has no Telegram
display name); the contract surfaces it as ``"Legacy profile"``, so that mapping
is done here — the route stays a thin JSON shaper. Any other profile keeps its
stored label (which is non-NULL in practice — bot profiles set it on creation).

Rules (UI_PLAN.md §9): no SQL in routes; one query, no N+1; ``muted_count`` is
derived from the stored ``muted_game_ids`` JSONB array without a join.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import StreamerPref

# The stable "default" profile key and the label surfaced for it (its stored
# label is NULL — it has no Telegram display name).
_DEFAULT_KEY = "default"
_DEFAULT_LABEL = "Legacy profile"


def _label_for(key: str, stored: str | None) -> str:
    """Display label: the legacy ``default`` profile maps its NULL to a name."""
    if stored:
        return str(stored)
    return _DEFAULT_LABEL if key == _DEFAULT_KEY else key


@dataclass(frozen=True, slots=True)
class UserRow:
    """One preference profile for the user switcher (API_CONTRACT.md)."""

    key: str
    label: str
    liked_genres: list[str] = field(default_factory=list)
    blocked_genres: list[str] = field(default_factory=list)
    subscribed_genres: list[str] = field(default_factory=list)
    muted_count: int = 0
    digest_enabled: bool = True
    created_at: datetime | None = None


async def profile_keys() -> set[str]:
    """The set of known preference-profile keys (the ``user_key`` allowlist).

    A tiny ``SELECT key`` — used to validate a ``user_key`` before an on-demand
    recommender run so an unknown profile is rejected (422) rather than silently
    scoring against empty prefs.
    """
    async with session_scope() as session:
        rows = (await session.execute(select(StreamerPref.key))).all()
    return {str(r[0]) for r in rows}


async def list_users() -> list[UserRow]:
    """Every preference profile, oldest first (stable order for the switcher).

    One ``SELECT`` over ``streamer_prefs``; ``muted_count`` is the length of the
    stored ``muted_game_ids`` array (no join). The ``"default"`` profile's NULL
    label is surfaced as ``"Legacy profile"`` per the contract.
    """
    stmt = select(
        StreamerPref.key,
        StreamerPref.label,
        StreamerPref.liked_genres,
        StreamerPref.blocked_genres,
        StreamerPref.subscribed_genres,
        StreamerPref.muted_game_ids,
        StreamerPref.digest_enabled,
        StreamerPref.created_at,
    ).order_by(StreamerPref.created_at.asc(), StreamerPref.id.asc())

    async with session_scope() as session:
        rows = (await session.execute(stmt)).all()

    return [
        UserRow(
            key=str(key),
            label=_label_for(str(key), label),
            liked_genres=list(liked or []),
            blocked_genres=list(blocked or []),
            subscribed_genres=list(subscribed or []),
            muted_count=len(muted or []),
            digest_enabled=bool(digest_enabled),
            created_at=created_at,
        )
        for key, label, liked, blocked, subscribed, muted, digest_enabled, created_at in rows
    ]
