"""Unit tests for the genre_sub component + digest genre quota (M7)."""

from __future__ import annotations

from datetime import UTC, datetime

from gamer.notify.digest import apply_genre_quota
from gamer.scoring.assembler import DEFAULT_WEIGHTS
from gamer.scoring.base import Candidate, ScoredRecommendation, ScoringContext
from gamer.scoring.components.genre_sub import GenreSubComponent

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


def _cand(gid: int, genres: list[str]) -> Candidate:
    return Candidate(game_id=gid, platform_app_id=1000 + gid, name=f"G{gid}", genres=genres)


# ── GenreSubComponent ─────────────────────────────────────────────────────────


async def test_genre_sub_matches_case_insensitively_reports_candidate_casing() -> None:
    comp = GenreSubComponent()
    ctx = ScoringContext(now=NOW, subscribed_genres=["puzzle"])
    cs = await comp.score(_cand(1, ["Puzzle", "Indie"]), ctx)
    assert cs.value == 1.0
    assert cs.reason == "subscribed genre: Puzzle"  # candidate's original casing
    assert cs.detail["matched"] == "Puzzle"


async def test_genre_sub_no_match_is_zero() -> None:
    comp = GenreSubComponent()
    ctx = ScoringContext(now=NOW, subscribed_genres=["Puzzle"])
    cs = await comp.score(_cand(1, ["RPG"]), ctx)
    assert cs.value == 0.0
    assert cs.reason == "not a subscribed genre"


async def test_genre_sub_empty_subscriptions_is_zero() -> None:
    comp = GenreSubComponent()
    ctx = ScoringContext(now=NOW, subscribed_genres=[])
    cs = await comp.score(_cand(1, ["Puzzle"]), ctx)
    assert cs.value == 0.0


def test_genre_sub_registered_with_weight() -> None:
    assert DEFAULT_WEIGHTS["genre_sub"] == 0.20
    from gamer.scoring.registry import discover_components

    keys = {c.key for c in discover_components()}
    assert "genre_sub" in keys


# ── apply_genre_quota (pure, exhaustive) ──────────────────────────────────────


def _rec(gid: int, score: float, genres: list[str]) -> ScoredRecommendation:
    return ScoredRecommendation(game_id=gid, name=f"G{gid}", score=score, genres=genres)


def test_quota_no_subscriptions_returns_plain_cut() -> None:
    ranked = [_rec(i, 1.0 - i * 0.1, ["RPG"]) for i in range(6)]
    assert apply_genre_quota(ranked, [], limit=3) == ranked[:3]


def test_quota_empty_inputs() -> None:
    assert apply_genre_quota([], ["Puzzle"], limit=3) == []


def test_quota_slots_zero_is_noop() -> None:
    ranked = [_rec(i, 1.0 - i * 0.1, ["Puzzle"]) for i in range(6)]
    assert apply_genre_quota(ranked, ["Puzzle"], limit=3, slots=0) == ranked[:3]


def test_quota_already_satisfied_returns_cut_unchanged() -> None:
    # Top 3 already contain 3 subscribed picks.
    ranked = [
        _rec(1, 0.9, ["Puzzle"]),
        _rec(2, 0.8, ["Puzzle"]),
        _rec(3, 0.7, ["Puzzle"]),
        _rec(4, 0.6, ["RPG"]),
    ]
    assert apply_genre_quota(ranked, ["Puzzle"], limit=3, slots=3) == ranked[:3]


def test_quota_promotes_subscribed_from_below_cut() -> None:
    ranked = [
        _rec(1, 0.90, ["RPG"]),
        _rec(2, 0.80, ["RPG"]),
        _rec(3, 0.70, ["RPG"]),
        _rec(4, 0.60, ["Puzzle"]),  # below cut, subscribed
        _rec(5, 0.50, ["Puzzle"]),
    ]
    out = apply_genre_quota(ranked, ["Puzzle"], limit=3, slots=1)
    # One subscribed pick promoted (game 4), replacing lowest non-sub in cut (game 3).
    ids = [r.game_id for r in out]
    assert 4 in ids
    assert 3 not in ids
    assert len(out) == 3
    # Result preserves score order.
    assert [r.score for r in out] == sorted((r.score for r in out), reverse=True)


def test_quota_fewer_available_than_slots() -> None:
    ranked = [
        _rec(1, 0.90, ["RPG"]),
        _rec(2, 0.80, ["RPG"]),
        _rec(3, 0.70, ["RPG"]),
        _rec(4, 0.60, ["Puzzle"]),  # only one subscribed anywhere
    ]
    out = apply_genre_quota(ranked, ["Puzzle"], limit=3, slots=3)
    # Only 1 subscribed exists → promote just that one, replacing lowest non-sub.
    ids = [r.game_id for r in out]
    assert 4 in ids
    assert len(out) == 3


def test_quota_case_insensitive_match() -> None:
    ranked = [
        _rec(1, 0.90, ["RPG"]),
        _rec(2, 0.80, ["RPG"]),
        _rec(3, 0.70, ["RPG"]),
        _rec(4, 0.60, ["PUZZLE"]),
    ]
    out = apply_genre_quota(ranked, ["puzzle"], limit=3, slots=1)
    assert 4 in [r.game_id for r in out]


def test_quota_no_subscribed_available_returns_cut() -> None:
    ranked = [_rec(i, 1.0 - i * 0.1, ["RPG"]) for i in range(5)]
    assert apply_genre_quota(ranked, ["Puzzle"], limit=3, slots=3) == ranked[:3]
