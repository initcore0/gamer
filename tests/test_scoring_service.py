"""Scorer service tests using in-memory fake components (no live DB)."""

from __future__ import annotations

from datetime import UTC, datetime

from gamer.scoring.base import (
    Candidate,
    ComponentScore,
    ScoreComponent,
    ScoringContext,
)
from gamer.scoring.penalties import BlocklistPenalty, CooldownPenalty
from gamer.scoring.service import build_assembler, default_penalties

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


class _FakeComponent:
    """A tiny component conforming to the ScoreComponent protocol.

    Returns a per-game value from a lookup table (default 0.0), so tests can
    control the ranking deterministically.
    """

    def __init__(self, key: str, values: dict[int, float]) -> None:
        self.key = key
        self._values = values

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        value = self._values.get(candidate.game_id, 0.0)
        return ComponentScore(value=value, reason=f"{self.key}={value:.2f}")


def _cand(game_id: int, name: str, genres: list[str] | None = None) -> Candidate:
    return Candidate(
        game_id=game_id, platform_app_id=1000 + game_id, name=name, genres=genres or []
    )


def test_fake_component_conforms_to_protocol() -> None:
    assert isinstance(_FakeComponent("momentum", {}), ScoreComponent)


def test_default_penalties_are_cooldown_and_blocklist() -> None:
    keys = {p.key for p in default_penalties()}
    assert keys == {"cooldown", "blocklist"}


async def test_build_assembler_injected_components_rank_by_score() -> None:
    momentum = _FakeComponent("momentum", {1: 0.2, 2: 0.9, 3: 0.5})
    assembler = build_assembler(components=[momentum], penalties=[])
    ctx = ScoringContext(now=NOW)
    cands = [_cand(1, "A"), _cand(2, "B"), _cand(3, "C")]
    ranked = await assembler.rank(cands, ctx)
    assert [r.name for r in ranked] == ["B", "C", "A"]
    # momentum weight is 0.35 by default; B's weighted value tops the list.
    assert ranked[0].score > ranked[1].score > ranked[2].score


async def test_muted_games_are_dropped() -> None:
    momentum = _FakeComponent("momentum", {1: 0.5, 2: 0.9})
    assembler = build_assembler(components=[momentum], penalties=[])
    ctx = ScoringContext(now=NOW, muted_game_ids={2})
    ranked = await assembler.rank([_cand(1, "A"), _cand(2, "Muted")], ctx)
    assert [r.name for r in ranked] == ["A"]


async def test_blocked_genre_is_suppressed_below_unblocked() -> None:
    momentum = _FakeComponent("momentum", {1: 0.9, 2: 0.4})
    assembler = build_assembler(components=[momentum], penalties=[BlocklistPenalty()])
    ctx = ScoringContext(now=NOW, blocked_genres=["Horror"])
    cands = [_cand(1, "Blocked", genres=["Horror"]), _cand(2, "Clean", genres=["RPG"])]
    ranked = await assembler.rank(cands, ctx)
    # Blocked game's score is multiplied to 0.0, so Clean ranks first.
    assert ranked[0].name == "Clean"
    blocked = next(r for r in ranked if r.name == "Blocked")
    assert blocked.score == 0.0
    assert "penalty:blocklist" in blocked.breakdown


async def test_cooldown_penalty_damps_recent_recommendation() -> None:
    momentum = _FakeComponent("momentum", {1: 0.9, 2: 0.9})
    assembler = build_assembler(components=[momentum], penalties=[CooldownPenalty()])
    ctx = ScoringContext(now=NOW, last_recommended={1: NOW})
    ranked = await assembler.rank([_cand(1, "JustRec"), _cand(2, "Fresh")], ctx)
    assert ranked[0].name == "Fresh"
    just = next(r for r in ranked if r.name == "JustRec")
    assert just.score == 0.0  # fully suppressed
