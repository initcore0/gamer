"""Pure decision-logic tests for the penalties (no DB, no clock)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gamer.scoring.base import Candidate, ScoringContext
from gamer.scoring.penalties import BlocklistPenalty, CooldownPenalty

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


def _cand(game_id: int = 1, genres: list[str] | None = None) -> Candidate:
    return Candidate(game_id=game_id, platform_app_id=1145360, name="Hades", genres=genres or [])


def _ctx(**kw: object) -> ScoringContext:
    return ScoringContext(now=NOW, **kw)  # type: ignore[arg-type]


# ── CooldownPenalty ────────────────────────────────────────────────────────────


async def test_cooldown_not_applied_when_never_recommended() -> None:
    pr = await CooldownPenalty().apply(_cand(), _ctx())
    assert pr.applied is False
    assert pr.multiplier == 1.0


async def test_cooldown_fully_suppressed_when_just_recommended() -> None:
    ctx = _ctx(last_recommended={1: NOW})
    pr = await CooldownPenalty(window=timedelta(days=7), floor=0.0).apply(_cand(), ctx)
    assert pr.applied is True
    assert pr.multiplier == 0.0
    assert "suppressed" in pr.reason


async def test_cooldown_scales_with_recency() -> None:
    window = timedelta(days=8)
    penalty = CooldownPenalty(window=window, floor=0.0)
    # Half the window elapsed → multiplier ≈ 0.5.
    ctx_mid = _ctx(last_recommended={1: NOW - timedelta(days=4)})
    mid = await penalty.apply(_cand(), ctx_mid)
    assert mid.applied is True
    assert abs(mid.multiplier - 0.5) < 1e-6

    # A quarter elapsed → smaller multiplier (stronger penalty).
    ctx_q = _ctx(last_recommended={1: NOW - timedelta(days=2)})
    quarter = await penalty.apply(_cand(), ctx_q)
    assert quarter.multiplier < mid.multiplier


async def test_cooldown_not_applied_after_window() -> None:
    penalty = CooldownPenalty(window=timedelta(days=7))
    ctx = _ctx(last_recommended={1: NOW - timedelta(days=10)})
    pr = await penalty.apply(_cand(), ctx)
    assert pr.applied is False
    assert pr.multiplier == 1.0


async def test_cooldown_respects_floor() -> None:
    penalty = CooldownPenalty(window=timedelta(days=7), floor=0.2)
    ctx = _ctx(last_recommended={1: NOW})
    pr = await penalty.apply(_cand(), ctx)
    assert pr.applied is True
    assert abs(pr.multiplier - 0.2) < 1e-6


# ── BlocklistPenalty ───────────────────────────────────────────────────────────


async def test_blocklist_suppresses_on_genre_match() -> None:
    ctx = _ctx(blocked_genres=["Horror"])
    pr = await BlocklistPenalty().apply(_cand(genres=["Action", "Horror"]), ctx)
    assert pr.applied is True
    assert pr.multiplier == 0.0
    assert "Horror" in pr.reason


async def test_blocklist_case_insensitive() -> None:
    ctx = _ctx(blocked_genres=["horror"])
    pr = await BlocklistPenalty().apply(_cand(genres=["Horror"]), ctx)
    assert pr.applied is True
    assert pr.multiplier == 0.0


async def test_blocklist_not_applied_without_match() -> None:
    ctx = _ctx(blocked_genres=["Horror"])
    pr = await BlocklistPenalty().apply(_cand(genres=["Action", "RPG"]), ctx)
    assert pr.applied is False
    assert pr.multiplier == 1.0


async def test_blocklist_not_applied_when_no_blocked_genres() -> None:
    pr = await BlocklistPenalty().apply(_cand(genres=["Horror"]), _ctx())
    assert pr.applied is False
    assert pr.multiplier == 1.0
