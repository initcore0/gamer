"""Chart-series query — rollup-aware time series (UI_PLAN.md §3.3, §5.5, UI-M3).

The game-detail charts fetch compact ``{ts, values}`` arrays from one endpoint.
Short ranges read raw ``signals_samples``; long ranges read the pre-aggregated
1d ``signals_rollups`` (avg), keeping the query cheap over a multi-million-row
samples table (§5.5).

Range allowlist (never interpolated — an :class:`StrEnum`, 422 on bad input):

    "24h", "7d"   → raw samples in the trailing window
    "30d", "all"  → 1d rollups (avg), trailing window / everything

``ts`` is epoch **seconds** (uPlot's native x axis), ``values`` are plain floats.
Both lists are parallel and ordered oldest→newest. Pure shaping is trivial; the
DB pass is integration-marked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import SignalMetric, SignalRollup, SignalSample


class SeriesMetric(StrEnum):
    """Allowlisted chart metrics (map to the underlying ``SignalMetric``)."""

    PLAYERS = "players"
    REVIEWS = "reviews"
    TWITCH = "twitch"


class SeriesRange(StrEnum):
    """Allowlisted chart ranges (raw samples ≤7d, 1d rollups beyond)."""

    H24 = "24h"
    D7 = "7d"
    D30 = "30d"
    ALL = "all"


_METRIC_MAP: dict[SeriesMetric, SignalMetric] = {
    SeriesMetric.PLAYERS: SignalMetric.PLAYERS,
    SeriesMetric.REVIEWS: SignalMetric.REVIEW_COUNT,
    SeriesMetric.TWITCH: SignalMetric.TWITCH_VIEWERS,
}

# Trailing window per range; ALL means "no lower bound".
_RANGE_WINDOW: dict[SeriesRange, timedelta | None] = {
    SeriesRange.H24: timedelta(hours=24),
    SeriesRange.D7: timedelta(days=7),
    SeriesRange.D30: timedelta(days=30),
    SeriesRange.ALL: None,
}

# Ranges served from the 1d rollups (avg); the rest read raw samples.
_ROLLUP_RANGES = {SeriesRange.D30, SeriesRange.ALL}


@dataclass(frozen=True, slots=True)
class Series:
    """Parallel epoch-seconds / float arrays for one chart line."""

    ts: list[int]
    values: list[float]


async def series(
    game_id: int,
    metric: SeriesMetric,
    range_key: SeriesRange,
    *,
    now: datetime | None = None,
) -> Series:
    """Return the ``(ts, values)`` series for one game/metric/range (UI_PLAN.md §5.5).

    ``metric``/``range_key`` are already allowlisted enums (the route types them
    so bad input 422s). Reads raw samples for ≤7d ranges and 1d rollups (avg) for
    30d/all. ``ts`` is epoch **seconds**; both lists are ordered oldest→newest.
    """
    now = now or datetime.now(UTC)
    signal_metric = _METRIC_MAP[metric]
    window = _RANGE_WINDOW[range_key]
    since = now - window if window is not None else None

    if range_key in _ROLLUP_RANGES:
        stmt = (
            select(SignalRollup.bucket_start, SignalRollup.avg)
            .where(SignalRollup.game_id == game_id)
            .where(SignalRollup.metric == signal_metric)
            .where(SignalRollup.window == "1d")
            .order_by(SignalRollup.bucket_start)
        )
        if since is not None:
            stmt = stmt.where(SignalRollup.bucket_start >= since)
    else:
        stmt = (
            select(SignalSample.ts, SignalSample.value)
            .where(SignalSample.game_id == game_id)
            .where(SignalSample.metric == signal_metric)
            .order_by(SignalSample.ts)
        )
        if since is not None:
            stmt = stmt.where(SignalSample.ts >= since)

    async with session_scope() as session:
        rows = (await session.execute(stmt)).all()

    ts = [int(dt.timestamp()) for dt, _v in rows]
    values = [float(v) for _dt, v in rows]
    return Series(ts=ts, values=values)
