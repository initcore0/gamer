"""DB-backed per-profile isolation for the scorer (multi-user).

Marked ``@integration`` — needs a migrated Postgres at head (0006). Proves that
recommendations are owned per profile (``pref_key``) and that one profile's
cooldown history never leaks into another's:

* ``recommend(key=A)`` persists rows stamped ``pref_key=A`` only.
* ``build_context`` for A sees A's rec in ``last_recommended``; for B it does not,
  so a game A just got recommended is *not* damped for B.

Run this file on its own (the shared async engine misbehaves batched with the
other integration files):
    uv run pytest tests/test_scoring_multiuser_integration.py -m integration
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, select

from gamer.db import session_scope
from gamer.db.models import Game, Platform, Recommendation, StreamerPref
from gamer.scoring.base import Candidate
from gamer.scoring.penalties import CooldownPenalty
from gamer.scoring.service import build_context, recommend

pytestmark = pytest.mark.integration

_KEY_A = "1111"  # a DM profile
_KEY_B = "2222"  # another DM profile
_APPID_BASE = 970000


async def _cleanup() -> None:
    async with session_scope() as session:
        ids = (
            (
                await session.execute(
                    select(Game.id).where(
                        Game.platform_app_id.between(_APPID_BASE, _APPID_BASE + 999)
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await session.execute(delete(Recommendation).where(Recommendation.game_id.in_(ids)))
            await session.execute(delete(Game).where(Game.id.in_(ids)))
        await session.execute(delete(StreamerPref).where(StreamerPref.key.in_([_KEY_A, _KEY_B])))


async def _seed_game(offset: int) -> int:
    async with session_scope() as session:
        game = Game(
            platform=Platform.STEAM,
            platform_app_id=_APPID_BASE + offset,
            name=f"MU Game {offset}",
            genres=["RPG"],
            tracked=True,
        )
        session.add(game)
        await session.flush()
        return game.id


async def test_recommend_stamps_pref_key_and_isolates_cooldown() -> None:
    await _cleanup()
    try:
        gid = await _seed_game(1)

        # Profile A gets a recommendation; it persists a row owned by A. The rec's
        # created_at is the DB server clock, so build the cooldown context at a
        # fresh "now" that is guaranteed >= it.
        recs_a = await recommend(limit=5, key=_KEY_A)
        assert recs_a, "A should get at least one pick"
        assert gid in {r.game_id for r in recs_a}
        now = datetime.now(UTC)

        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Recommendation.pref_key, Recommendation.game_id).where(
                        Recommendation.game_id == gid
                    )
                )
            ).all()
        # Exactly A's rows exist for this game; every one is stamped with A's key.
        assert rows, "A's rec must be persisted"
        assert {pref_key for pref_key, _ in rows} == {_KEY_A}

        # Cooldown context: A sees its own rec in last_recommended for this game;
        # B does not (no cross-profile pollution).
        async with session_scope() as session:
            ctx_a = await build_context(session, now=now, key=_KEY_A)
            ctx_b = await build_context(session, now=now, key=_KEY_B)
        assert gid in ctx_a.last_recommended
        assert gid not in ctx_b.last_recommended

        # And the cooldown penalty therefore damps A's re-pick but not B's.
        cooldown = CooldownPenalty()
        cand = Candidate(game_id=gid, platform_app_id=_APPID_BASE + 1, name="MU Game 1", genres=[])
        pen_a = await cooldown.apply(cand, ctx_a)
        pen_b = await cooldown.apply(cand, ctx_b)
        assert pen_a.applied and pen_a.multiplier < 1.0  # A: on cooldown
        assert not pen_b.applied and pen_b.multiplier == 1.0  # B: fresh, no penalty
    finally:
        await _cleanup()


async def test_legacy_adoption_seeds_operator_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's DM profile adopts the legacy 'default' profile's fields on
    first creation (subscriptions/mutes carry over the multi-user migration), and
    the 'default' row is never deleted."""
    from gamer.bot import handlers

    dm_key = "3333"
    monkeypatch.setenv("GAMER_TELEGRAM__DM_CHAT_ID", dm_key)
    monkeypatch.setenv("GAMER_TELEGRAM__GROUP_CHAT_ID", "-4444")
    from gamer.config import get_settings

    # The autouse fixture already clears the cache before this test; clear again so
    # the freshly-set DM/group ids are picked up by _should_adopt_legacy.
    get_settings.cache_clear()

    async def _wipe() -> None:
        async with session_scope() as session:
            await session.execute(
                delete(StreamerPref).where(StreamerPref.key.in_(["default", dm_key]))
            )

    await _wipe()
    try:
        # Seed a legacy profile with subscriptions + mutes.
        async with session_scope() as session:
            session.add(
                StreamerPref(
                    key="default",
                    subscribed_genres=["Puzzle"],
                    muted_game_ids=[42],
                    digest_enabled=False,
                )
            )

        # The operator's DM profile is created on demand and adopts the legacy row.
        adopted = await handlers._get_prefs(dm_key, label="Operator")
        assert adopted.subscribed_genres == ["Puzzle"]
        assert adopted.muted_game_ids == [42]
        assert adopted.digest_enabled is False
        assert adopted.label == "Operator"

        # The legacy row is untouched (never deleted/modified by adoption).
        async with session_scope() as session:
            legacy = (
                await session.execute(select(StreamerPref).where(StreamerPref.key == "default"))
            ).scalar_one()
            assert legacy.subscribed_genres == ["Puzzle"]

        # A non-operator chat does NOT adopt — it starts blank.
        stranger_key = "9999"
        async with session_scope() as session:
            await session.execute(delete(StreamerPref).where(StreamerPref.key == stranger_key))
        stranger = await handlers._get_prefs(stranger_key, label="Stranger")
        assert stranger.subscribed_genres == []
        assert stranger.muted_game_ids == []
        async with session_scope() as session:
            await session.execute(delete(StreamerPref).where(StreamerPref.key == stranger_key))
    finally:
        await _wipe()
        get_settings.cache_clear()
