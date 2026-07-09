"""Backtest harness — replay the recommender as of a past instant (PLAN.md §4.5).

The whole point of injecting ``ScoringContext.now`` is this: to see what the
recommender *would* have picked at some past moment, we set ``now = as_of`` and
let the (``now``-aware) components consider only samples/news with ``ts <= now``.
Nothing is persisted — this is an eyeball-the-picks tool, not a run of record.

``backtest`` accepts injectable ``components`` / ``candidates`` (used by tests for
determinism and to avoid a live DB); when omitted it discovers components and
loads tracked games from the DB, exactly like the live service.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from gamer.db import session_scope
from gamer.logging import get_logger
from gamer.scoring.base import (
    Candidate,
    Penalty,
    ScoreComponent,
    ScoredRecommendation,
    ScoringContext,
)
from gamer.scoring.service import build_assembler, build_context, load_candidates

log = get_logger("scoring.backtest")


async def backtest(
    as_of: datetime,
    weeks: int,
    *,
    limit: int = 5,
    key: str = "default",
    components: Sequence[ScoreComponent] | None = None,
    penalties: Sequence[Penalty] | None = None,
    candidates: Sequence[Candidate] | None = None,
) -> list[ScoredRecommendation]:
    """Replay the recommender as of ``as_of`` and return what it would have picked.

    ``weeks`` bounds how far back the replay window reaches (``as_of - weeks``);
    it is recorded on the context for components that honor a lookback, and logged
    for the operator. Results are ranked but never persisted.

    Pass ``candidates`` (and typically ``components``) to run fully in-memory with
    no DB — this is what the tests use for deterministic assertions.
    """
    assembler = build_assembler(components=components, penalties=penalties)
    window_start = as_of - timedelta(weeks=weeks)
    log.info("backtest_start", as_of=as_of.isoformat(), weeks=weeks, since=window_start.isoformat())

    if candidates is not None:
        cands = list(candidates)
        # Build a minimal context without touching the DB.
        ctx = ScoringContext(now=as_of)
        ranked = await assembler.rank(cands, ctx, limit=limit)
    else:
        async with session_scope() as session:
            cands = await load_candidates(session)
            ctx = await build_context(session, now=as_of, key=key)
            ranked = await assembler.rank(cands, ctx, limit=limit)

    log.info("backtest_done", as_of=as_of.isoformat(), returned=len(ranked))
    return ranked


def _format_picks(as_of: datetime, ranked: Sequence[ScoredRecommendation]) -> str:
    lines = [f"Backtest picks as of {as_of.isoformat()}:"]
    if not ranked:
        lines.append("  (no candidates / no components loaded)")
        return "\n".join(lines)
    for i, r in enumerate(ranked, start=1):
        lines.append(f"\n{i}. {r.name} — score {r.score:.3f}")
        for line in r.why().splitlines()[1:]:
            lines.append(f"  {line}")
    return "\n".join(lines)


def _parse_as_of(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


async def _amain(as_of: datetime, weeks: int, limit: int, key: str) -> None:
    ranked = await backtest(as_of, weeks, limit=limit, key=key)
    print(_format_picks(as_of, ranked))


def main(argv: Sequence[str] | None = None) -> None:
    """CLI: print the picks + breakdowns the recommender would have made."""
    parser = argparse.ArgumentParser(description="Replay the recommender as of a past instant.")
    parser.add_argument(
        "--as-of",
        type=_parse_as_of,
        default=datetime.now(UTC),
        help="ISO instant to replay at (default: now).",
    )
    parser.add_argument("--weeks", type=int, default=4, help="Lookback window in weeks.")
    parser.add_argument("--limit", type=int, default=5, help="Number of picks to show.")
    parser.add_argument("--key", default="default", help="Streamer prefs key.")
    args = parser.parse_args(argv)
    asyncio.run(_amain(args.as_of, args.weeks, args.limit, args.key))


if __name__ == "__main__":  # pragma: no cover
    main()
