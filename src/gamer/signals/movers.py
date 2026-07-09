"""Naive "top movers" — the M2 digest signal, before the real scorer (M3).

Computes each tracked game's player-count change over a lookback window from
``signals_samples`` and returns the biggest gainers. This is deliberately simple
(a raw delta / percent change), wired from real M1 data so the end-to-end
Telegram path can ship before scoring exists (PLAN.md M2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import Game, SignalMetric, SignalSample


@dataclass(slots=True)
class Mover:
    game_id: int
    name: str
    platform_app_id: int
    latest: float
    baseline: float

    @property
    def delta(self) -> float:
        return self.latest - self.baseline

    @property
    def pct(self) -> float | None:
        if self.baseline <= 0:
            return None
        return (self.delta / self.baseline) * 100.0


@dataclass(slots=True)
class _Accum:
    """Per-game fold of the samples seen within the window (ordered by ts)."""

    name: str
    app_id: int
    baseline: float
    latest: float
    count: int = field(default=1)


async def top_movers(
    *,
    limit: int = 10,
    window: timedelta = timedelta(days=1),
    metric: SignalMetric = SignalMetric.PLAYERS,
    now: datetime | None = None,
) -> list[Mover]:
    """Biggest player-count gainers over ``window``.

    For each game we take its most recent sample as ``latest`` and its oldest
    sample within the window as ``baseline``, then rank by absolute delta. Games
    with only one sample (no movement to measure) are skipped.
    """
    now = now or datetime.now(UTC)
    since = now - window

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    SignalSample.game_id,
                    SignalSample.value,
                    Game.name,
                    Game.platform_app_id,
                )
                .join(Game, Game.id == SignalSample.game_id)
                .where(SignalSample.metric == metric)
                .where(SignalSample.ts >= since)
                .order_by(SignalSample.game_id, SignalSample.ts)
            )
        ).all()

    by_game: dict[int, _Accum] = {}
    for game_id, value, name, app_id in rows:
        acc = by_game.get(game_id)
        if acc is None:
            by_game[game_id] = _Accum(name=name, app_id=app_id, baseline=value, latest=value)
        else:
            acc.latest = value
            acc.count += 1

    movers = [
        Mover(
            game_id=gid,
            name=acc.name,
            platform_app_id=acc.app_id,
            latest=acc.latest,
            baseline=acc.baseline,
        )
        for gid, acc in by_game.items()
        if acc.count > 1
    ]
    movers.sort(key=lambda m: m.delta, reverse=True)
    return movers[:limit]
