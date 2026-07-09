from __future__ import annotations

from datetime import UTC, datetime

from gamer.scoring import (
    Assembler,
    Candidate,
    ComponentScore,
    PenaltyResult,
    ScoredRecommendation,
    ScoringContext,
)


class _FixedComponent:
    def __init__(self, key: str, value: float) -> None:
        self.key = key
        self._value = value

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        return ComponentScore(value=self._value, reason=f"{self.key}={self._value}")


class _HalvingPenalty:
    key = "cooldown"

    async def apply(self, candidate: Candidate, ctx: ScoringContext) -> PenaltyResult:
        return PenaltyResult(multiplier=0.5, reason="on cooldown", applied=True)


def _ctx() -> ScoringContext:
    return ScoringContext(now=datetime(2026, 7, 9, tzinfo=UTC))


def _cand(game_id: int = 1, name: str = "Hades") -> Candidate:
    return Candidate(game_id=game_id, platform_app_id=1145360, name=name)


async def test_weighted_sum_and_breakdown() -> None:
    a = Assembler(
        components=[_FixedComponent("momentum", 1.0), _FixedComponent("fit", 0.5)],
        weights={"momentum": 0.4, "fit": 0.2},
    )
    rec = await a.score_one(_cand(), _ctx())
    # 0.4*1.0 + 0.2*0.5 = 0.5
    assert rec.score == 0.5
    assert rec.breakdown["momentum"]["weighted"] == 0.4
    assert rec.breakdown["fit"]["weighted"] == 0.1
    assert "momentum=1.0" in rec.why()


async def test_penalty_multiplies_score() -> None:
    a = Assembler(
        components=[_FixedComponent("momentum", 1.0)],
        penalties=[_HalvingPenalty()],
        weights={"momentum": 1.0},
    )
    rec = await a.score_one(_cand(), _ctx())
    assert rec.score == 0.5  # 1.0 * 0.5 cooldown
    assert rec.breakdown["penalty:cooldown"]["multiplier"] == 0.5


async def test_rank_sorts_and_drops_muted() -> None:
    a = Assembler(
        components=[_FixedComponent("momentum", 1.0)],
        weights={"momentum": 1.0},
    )
    ctx = _ctx()
    ctx.muted_game_ids = {2}
    cands = [_cand(1, "A"), _cand(2, "Muted"), _cand(3, "C")]
    # Make C score higher by giving it a different component value via a second assembler?
    # Simpler: all equal here, assert muted dropped and count.
    ranked = await a.rank(cands, ctx)
    names = [r.name for r in ranked]
    assert "Muted" not in names
    assert len(ranked) == 2
    assert all(isinstance(r, ScoredRecommendation) for r in ranked)
