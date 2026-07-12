"""DB-backed preference-profile query tests (API_CONTRACT.md — user switcher).

Marked ``@integration`` — requires a migrated Postgres via GAMER_DB__*. Verifies
``list_users`` shapes each ``streamer_prefs`` row (label mapping, muted_count)
and ``profile_keys`` returns the key allowlist. Runnable individually.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from gamer.api.queries.users import list_users, profile_keys
from gamer.db import session_scope
from gamer.db.models import StreamerPref

pytestmark = pytest.mark.integration

_DEFAULT = "default"
_USER = "user_it_777"


async def _cleanup() -> None:
    async with session_scope() as session:
        await session.execute(delete(StreamerPref).where(StreamerPref.key.in_([_DEFAULT, _USER])))


async def _seed() -> None:
    await _cleanup()
    async with session_scope() as session:
        session.add(
            StreamerPref(
                key=_DEFAULT,
                label=None,  # legacy profile has no label
                liked_genres=[],
                blocked_genres=[],
                subscribed_genres=["Puzzle"],
                muted_game_ids=[1, 2, 3],
                digest_enabled=True,
            )
        )
        session.add(
            StreamerPref(
                key=_USER,
                label="Grace",
                liked_genres=["Roguelike"],
                blocked_genres=["Sports"],
                subscribed_genres=[],
                muted_game_ids=[],
                digest_enabled=False,
            )
        )


async def test_list_users_shapes_rows() -> None:
    await _seed()
    try:
        rows = await list_users()
        by_key = {r.key: r for r in rows}
        assert _DEFAULT in by_key and _USER in by_key

        default = by_key[_DEFAULT]
        assert default.label == "Legacy profile"  # NULL → mapped
        assert default.subscribed_genres == ["Puzzle"]
        assert default.muted_count == 3
        assert default.digest_enabled is True
        assert default.created_at is not None

        user = by_key[_USER]
        assert user.label == "Grace"
        assert user.liked_genres == ["Roguelike"]
        assert user.blocked_genres == ["Sports"]
        assert user.muted_count == 0
        assert user.digest_enabled is False
    finally:
        await _cleanup()


async def test_profile_keys_returns_allowlist() -> None:
    await _seed()
    try:
        keys = await profile_keys()
        assert {_DEFAULT, _USER} <= keys
    finally:
        await _cleanup()
