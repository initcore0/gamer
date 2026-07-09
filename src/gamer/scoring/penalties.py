"""Penalties — multiplicative down-weights applied after the weighted sum.

Penalties implement :class:`~gamer.scoring.base.Penalty`: given a candidate and
the request context they return a :class:`~gamer.scoring.base.PenaltyResult` whose
``multiplier`` scales the assembled score (1.0 = no effect, 0.0 = fully
suppressed). The decision logic is kept pure (no DB, no clock) so it is trivially
unit-testable — the context carries ``now`` and ``last_recommended``.

Implemented here (PLAN.md §4.5):

  * :class:`CooldownPenalty` — recently recommended games are damped, scaled by
    how recently they were recommended (just recommended → fully suppressed;
    older than the window → no effect).
  * :class:`BlocklistPenalty` — a candidate whose genres intersect the streamer's
    blocked genres is fully suppressed.

Muting is NOT handled here: the assembler drops muted ids from the ranking
entirely, so a mute penalty would be redundant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from gamer.scoring.base import Candidate, PenaltyResult, ScoringContext


@dataclass(slots=True)
class CooldownPenalty:
    """Damp games we recommended recently, scaled by recency.

    If we last recommended ``game_id`` at ``t``, and ``ctx.now - t`` is within
    ``window``, the multiplier ramps linearly from ``floor`` (just recommended)
    up to ``1.0`` (at/after the window edge). Outside the window — or if the game
    was never recommended — the penalty does not apply.

    ``floor`` is the strongest damping (0.0 = fully suppressed right after a
    recommendation). Recency fraction ``f = elapsed / window`` in ``[0, 1]``, and
    ``multiplier = floor + (1 - floor) * f``.
    """

    key: str = "cooldown"
    window: timedelta = timedelta(days=7)
    floor: float = 0.0

    def _multiplier(self, elapsed: timedelta) -> float:
        if elapsed >= self.window or elapsed < timedelta(0):
            return 1.0
        fraction = elapsed / self.window
        return self.floor + (1.0 - self.floor) * fraction

    async def apply(self, candidate: Candidate, ctx: ScoringContext) -> PenaltyResult:
        last = ctx.last_recommended.get(candidate.game_id)
        if last is None:
            return PenaltyResult(multiplier=1.0, reason="never recommended", applied=False)

        elapsed = ctx.now - last
        multiplier = self._multiplier(elapsed)
        if multiplier >= 1.0:
            return PenaltyResult(multiplier=1.0, reason="cooldown window elapsed", applied=False)

        hours = elapsed.total_seconds() / 3600.0
        if multiplier <= 0.0:
            reason = "just recommended — fully suppressed"
        else:
            reason = f"recommended {hours:.0f}h ago — on cooldown"
        return PenaltyResult(multiplier=round(multiplier, 4), reason=reason, applied=True)


@dataclass(slots=True)
class BlocklistPenalty:
    """Fully suppress candidates whose genres intersect the blocklist."""

    key: str = "blocklist"

    async def apply(self, candidate: Candidate, ctx: ScoringContext) -> PenaltyResult:
        if not ctx.blocked_genres:
            return PenaltyResult(multiplier=1.0, reason="no blocked genres", applied=False)

        blocked = {g.casefold() for g in ctx.blocked_genres}
        hit = sorted({g for g in candidate.genres if g.casefold() in blocked})
        if not hit:
            return PenaltyResult(multiplier=1.0, reason="no blocked genre match", applied=False)

        return PenaltyResult(
            multiplier=0.0,
            reason=f"blocked genre: {', '.join(hit)}",
            applied=True,
        )
