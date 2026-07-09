"""Backtest harness tests — deterministic replay with injected candidates."""

from __future__ import annotations

from datetime import UTC, datetime

from gamer.scoring.backtest import backtest
from gamer.scoring.base import Candidate, ComponentScore, ScoringContext

AS_OF = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


class _NowAwareComponent:
    """A component whose value depends on ctx.now — proves now-injection works."""

    key = "momentum"

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        # A silly but deterministic function of now + game_id.
        value = ((ctx.now.month + candidate.game_id) % 10) / 10.0
        return ComponentScore(value=value, reason=f"now={ctx.now.isoformat()}")


def _cands() -> list[Candidate]:
    return [
        Candidate(game_id=1, platform_app_id=1001, name="A"),
        Candidate(game_id=2, platform_app_id=1002, name="B"),
        Candidate(game_id=3, platform_app_id=1003, name="C"),
    ]


async def test_backtest_is_deterministic() -> None:
    comp = _NowAwareComponent()
    r1 = await backtest(AS_OF, weeks=4, components=[comp], penalties=[], candidates=_cands())
    r2 = await backtest(AS_OF, weeks=4, components=[comp], penalties=[], candidates=_cands())
    assert [(r.name, r.score) for r in r1] == [(r.name, r.score) for r in r2]


async def test_backtest_injects_as_of_into_context() -> None:
    comp = _NowAwareComponent()
    ranked = await backtest(AS_OF, weeks=4, components=[comp], penalties=[], candidates=_cands())
    assert ranked
    # The component embedded ctx.now in its reason; confirm as_of was used.
    assert AS_OF.isoformat() in ranked[0].breakdown["momentum"]["reason"]


async def test_backtest_respects_limit() -> None:
    comp = _NowAwareComponent()
    ranked = await backtest(
        AS_OF, weeks=4, limit=2, components=[comp], penalties=[], candidates=_cands()
    )
    assert len(ranked) == 2


async def test_backtest_different_as_of_changes_picks() -> None:
    comp = _NowAwareComponent()
    cands = _cands()
    early = await backtest(
        datetime(2026, 1, 1, tzinfo=UTC), weeks=4, components=[comp], penalties=[], candidates=cands
    )
    late = await backtest(
        datetime(2026, 9, 1, tzinfo=UTC), weeks=4, components=[comp], penalties=[], candidates=cands
    )
    # now-dependent component → different scores across replay instants.
    assert [r.score for r in early] != [r.score for r in late]
