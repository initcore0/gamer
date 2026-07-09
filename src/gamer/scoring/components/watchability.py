"""Watchability score component (PLAN.md §4.5).

Watchability is the Twitch-viewers-to-players ratio: a game that is *watched* far
more than it is *played* streams well (high spectator demand per active player),
which is exactly the signal a streamer wants when picking what to play next.

The component reads the latest ``TWITCH_VIEWERS`` and ``PLAYERS`` samples for the
game (relative to ``ctx.now``, never ``datetime.now()``) and turns their ratio
into a normalized ``[0, 1]`` value. All the arithmetic lives in a small **pure**
helper (:func:`watchability_value`) that takes plain numbers, so it is unit
testable without a database.
"""

from __future__ import annotations

from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import SignalMetric, SignalSample
from gamer.logging import get_logger
from gamer.scoring.base import Candidate, ComponentScore, ScoringContext

log = get_logger("scoring.components.watchability")

# ── Tunable constants ─────────────────────────────────────────────────────────

#: Viewers-per-player ratio that saturates the watchability signal to ~0.5.
#: A ratio of ``WATCHABILITY_RATIO_SATURATION`` maps to 0.5; higher ratios
#: approach 1.0 asymptotically. Most games sit well below 1.0 viewers/player,
#: so a modest saturation makes the signal responsive in the interesting range.
WATCHABILITY_RATIO_SATURATION = 0.5


# ── Pure math helper (DB-free, unit-testable) ─────────────────────────────────


def watchability_value(viewers: float, players: float) -> float:
    """Squash a Twitch-viewers / players ratio into ``[0, 1]``.

    ``ratio = viewers / max(players, 1)`` (so a game with no recorded players
    still yields a finite ratio driven by viewers). The ratio is mapped through a
    saturating curve ``ratio / (ratio + saturation)`` giving diminishing returns:
    ``ratio == WATCHABILITY_RATIO_SATURATION`` → 0.5, large ratios → ~1.0, and a
    non-positive ratio → 0.0.
    """
    if viewers <= 0.0:
        return 0.0
    ratio = viewers / max(players, 1.0)
    if ratio <= 0.0:
        return 0.0
    return ratio / (ratio + WATCHABILITY_RATIO_SATURATION)


# ── Component ─────────────────────────────────────────────────────────────────


class WatchabilityComponent:
    """Twitch-viewers-to-players ratio: streams well vs. plays well.

    Fetches the game's most recent ``TWITCH_VIEWERS`` and ``PLAYERS`` samples (at
    or before ``ctx.now``) and delegates the ratio math to
    :func:`watchability_value`. No Twitch data → value ``0.0`` with reason
    ``"no Twitch data"``.
    """

    key = "watchability"

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        async with session_scope() as session:
            viewers = await self._latest(
                session, candidate.game_id, SignalMetric.TWITCH_VIEWERS, ctx
            )
            players = await self._latest(session, candidate.game_id, SignalMetric.PLAYERS, ctx)
        return self._evaluate(viewers, players)

    @staticmethod
    async def _latest(
        session: object,
        game_id: int,
        metric: SignalMetric,
        ctx: ScoringContext,
    ) -> float | None:
        value = (
            await session.execute(  # type: ignore[attr-defined]
                select(SignalSample.value)
                .where(SignalSample.game_id == game_id)
                .where(SignalSample.metric == metric)
                .where(SignalSample.ts <= ctx.now)
                .order_by(SignalSample.ts.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return None if value is None else float(value)

    def _evaluate(self, viewers: float | None, players: float | None) -> ComponentScore:
        """Pure evaluation of the latest viewers/players samples → ComponentScore."""
        if viewers is None:
            return ComponentScore(
                value=0.0,
                reason="no Twitch data",
                detail={"viewers": None, "players": players, "ratio": None},
            )

        players_val = players if players is not None else 0.0
        ratio = viewers / max(players_val, 1.0)
        value = watchability_value(viewers, players_val)

        reason = (
            f"{viewers:.0f} viewers / {players_val:.0f} players (ratio {ratio:.2f})"
            if players_val > 0.0
            else f"{viewers:.0f} viewers, no player baseline (ratio {ratio:.2f})"
        )

        return ComponentScore(
            value=value,
            reason=reason,
            detail={
                "viewers": viewers,
                "players": players_val,
                "ratio": round(ratio, 4),
            },
        )
