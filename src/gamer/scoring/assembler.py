"""Score assembler — composes weighted components + penalties into a ranked list.

The assembler is the stable core of the recommender: component agents implement
:class:`~gamer.scoring.base.ScoreComponent` / ``Penalty`` and register them here;
the assembler weights, combines, applies penalties, and records the full
breakdown. It has no knowledge of *how* any component computes its value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gamer.logging import get_logger
from gamer.scoring.base import (
    Candidate,
    Penalty,
    ScoreComponent,
    ScoredRecommendation,
    ScoringContext,
)

log = get_logger("scoring.assembler")

# Default component weights (PLAN.md §4.5). Sum need not be 1; the final score is
# the weighted sum, penalties are multiplicative. Tunable without code changes.
DEFAULT_WEIGHTS: dict[str, float] = {
    "momentum": 0.35,
    "hype": 0.25,
    "watchability": 0.15,
    "freshness": 0.10,
    "fit": 0.15,
}


@dataclass(slots=True)
class Assembler:
    components: list[ScoreComponent]
    penalties: list[Penalty] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))

    async def score_one(self, candidate: Candidate, ctx: ScoringContext) -> ScoredRecommendation:
        breakdown: dict[str, object] = {}
        total = 0.0

        for component in self.components:
            weight = self.weights.get(component.key, 0.0)
            cs = await component.score(candidate, ctx)
            weighted = weight * cs.value
            total += weighted
            breakdown[component.key] = {
                "weight": weight,
                "value": round(cs.value, 4),
                "weighted": round(weighted, 4),
                "reason": cs.reason,
                "detail": cs.detail,
            }

        for penalty in self.penalties:
            pr = await penalty.apply(candidate, ctx)
            if pr.applied and pr.multiplier != 1.0:
                total *= pr.multiplier
                breakdown[f"penalty:{penalty.key}"] = {
                    "multiplier": round(pr.multiplier, 4),
                    "reason": pr.reason,
                }

        return ScoredRecommendation(
            game_id=candidate.game_id,
            name=candidate.name,
            score=round(total, 4),
            breakdown=breakdown,
        )

    async def rank(
        self,
        candidates: list[Candidate],
        ctx: ScoringContext,
        *,
        limit: int | None = None,
    ) -> list[ScoredRecommendation]:
        """Score all candidates and return them sorted by score, best first.

        Muted games are dropped entirely (not merely penalized) so they never
        surface in a recommendation list.
        """
        scored = [
            await self.score_one(c, ctx) for c in candidates if c.game_id not in ctx.muted_game_ids
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        if limit is not None:
            scored = scored[:limit]
        log.info("ranked", candidates=len(candidates), returned=len(scored))
        return scored
