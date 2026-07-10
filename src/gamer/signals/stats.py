"""Precomputed catalog-row stats refresh (UI_PLAN.md §5.4, UI-M2).

The catalog list page must never aggregate ``signals_samples`` per row at
request time. This job rolls the last 7 days of samples into one small
``game_stats`` row per game — latest player count, 24h delta, a downsampled
7-day PLAYERS sparkline, latest review count / Twitch viewers, and the last
signal time — then upserts it. The list query just LEFT JOINs the result.

Registered as ``stats:refresh`` every 15 min (see :mod:`gamer.jobs`).

Design (matches the enrichment job): the number-crunching lives in DB-free pure
helpers (:func:`downsample_spark`, :func:`compute_delta`, :func:`build_stats_row`)
that are exhaustively unit-tested; the DB pass (:func:`refresh_game_stats`) is
integration-marked and processes games in bounded ``game_id`` batches so memory
stays flat over a multi-million-row samples table.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from gamer.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("signals.stats")

# Sparkline: 7 days bucketed into 8-hour windows → at most 21 points.
_SPARK_WINDOW = timedelta(days=7)
_SPARK_BUCKET = timedelta(hours=8)
MAX_SPARK_POINTS = 21
# 24h-delta baseline: the latest sample at least this old.
_DELTA_LAG = timedelta(hours=24)
# Bounded per-batch game count so a full refresh never loads everything at once.
BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class Sample:
    """One (ts, value) PLAYERS sample — the pure helpers' only input shape."""

    ts: datetime
    value: float


@dataclass(frozen=True, slots=True)
class StatsRow:
    """The computed stats for one game (mirrors the ``game_stats`` columns)."""

    current_players: float | None
    players_24h_delta: float | None
    players_7d_spark: list[float]
    review_count: float | None
    twitch_viewers: float | None
    last_signal_at: datetime | None


def downsample_spark(
    samples: Sequence[Sample],
    *,
    now: datetime,
    window: timedelta = _SPARK_WINDOW,
    bucket: timedelta = _SPARK_BUCKET,
) -> list[float]:
    """Average PLAYERS samples into fixed time buckets, oldest→newest.

    Samples are bucketed by ``bucket``-wide windows anchored at ``now - window``;
    each non-empty bucket contributes its mean. Empty buckets are skipped (so a
    sparse game yields a short line, not a run of gaps), yielding ≤ ``window /
    bucket`` points. Samples outside ``[now - window, now]`` are ignored.
    """
    start = now - window
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for s in samples:
        if s.ts < start or s.ts > now:
            continue
        idx = int((s.ts - start) / bucket)
        sums[idx] = sums.get(idx, 0.0) + s.value
        counts[idx] = counts.get(idx, 0) + 1
    return [sums[i] / counts[i] for i in sorted(sums)]


def compute_delta(
    samples: Sequence[Sample],
    *,
    now: datetime,
    lag: timedelta = _DELTA_LAG,
) -> float | None:
    """Latest value minus the latest sample at least ``lag`` old.

    Returns ``None`` when there is no sample old enough to form a baseline (so a
    brand-new game shows no delta rather than a spurious one). ``samples`` need
    not be sorted.
    """
    if not samples:
        return None
    latest = max(samples, key=lambda s: s.ts)
    cutoff = now - lag
    baselines = [s for s in samples if s.ts <= cutoff]
    if not baselines:
        return None
    baseline = max(baselines, key=lambda s: s.ts)
    return latest.value - baseline.value


def _latest_value(samples: Sequence[Sample]) -> float | None:
    if not samples:
        return None
    return max(samples, key=lambda s: s.ts).value


def build_stats_row(
    *,
    players: Sequence[Sample],
    reviews: Sequence[Sample],
    twitch: Sequence[Sample],
    last_signal_at: datetime | None,
    now: datetime,
) -> StatsRow:
    """Assemble a :class:`StatsRow` from one game's per-metric samples (DB-free).

    ``players``/``reviews``/``twitch`` are the game's samples for the matching
    metric within the 7-day window; ``last_signal_at`` is the max ts of *any*
    metric's samples (computed by the caller, which sees all metrics).
    """
    return StatsRow(
        current_players=_latest_value(players),
        players_24h_delta=compute_delta(players, now=now),
        players_7d_spark=downsample_spark(players, now=now),
        review_count=_latest_value(reviews),
        twitch_viewers=_latest_value(twitch),
        last_signal_at=last_signal_at,
    )


# ── DB pass (integration-marked) ─────────────────────────────────────────────


async def _game_ids_to_refresh(session: AsyncSession, since: datetime) -> list[int]:
    """Ids of games with a sample since ``since`` OR already in ``game_stats``."""
    from sqlalchemy import select, union

    from gamer.db.models import GameStats, SignalSample

    recent = select(SignalSample.game_id).where(SignalSample.ts >= since)
    existing = select(GameStats.game_id)
    unioned = union(recent, existing).subquery()
    stmt = select(unioned.c.game_id).order_by(unioned.c.game_id)
    rows = (await session.execute(stmt)).all()
    return [int(r[0]) for r in rows]


async def refresh_game_stats(*, now: datetime | None = None) -> int:
    """Recompute + upsert ``game_stats`` for every recently-active game.

    "Recently active" = has at least one ``signals_samples`` row in the last 7
    days, plus every game already present in ``game_stats`` (so a game that goes
    quiet still gets its stats aged/zeroed rather than left stale). Returns the
    number of games upserted.

    Processes game ids in ``BATCH_SIZE`` chunks and, per chunk, loads only that
    chunk's 7-day samples — memory stays bounded regardless of catalog size.
    """
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from gamer.db import session_scope
    from gamer.db.models import GameStats, SignalMetric, SignalSample

    now = now or datetime.now(UTC)
    since = now - _SPARK_WINDOW

    async with session_scope() as session:
        ids = await _game_ids_to_refresh(session, since)

    total = 0
    for start in range(0, len(ids), BATCH_SIZE):
        batch = ids[start : start + BATCH_SIZE]
        if not batch:
            continue
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(
                        SignalSample.game_id,
                        SignalSample.metric,
                        SignalSample.ts,
                        SignalSample.value,
                    )
                    .where(SignalSample.game_id.in_(batch))
                    .where(SignalSample.ts >= since)
                    .order_by(SignalSample.game_id)
                )
            ).all()

            per_game: dict[int, dict[SignalMetric, list[Sample]]] = {gid: {} for gid in batch}
            last_seen: dict[int, datetime] = {}
            for game_id, metric, ts, value in rows:
                metric_enum = metric if isinstance(metric, SignalMetric) else SignalMetric(metric)
                per_game[game_id].setdefault(metric_enum, []).append(Sample(ts=ts, value=value))
                prev = last_seen.get(game_id)
                if prev is None or ts > prev:
                    last_seen[game_id] = ts

            values = []
            for gid in batch:
                metrics = per_game[gid]
                row = build_stats_row(
                    players=metrics.get(SignalMetric.PLAYERS, []),
                    reviews=metrics.get(SignalMetric.REVIEW_COUNT, []),
                    twitch=metrics.get(SignalMetric.TWITCH_VIEWERS, []),
                    last_signal_at=last_seen.get(gid),
                    now=now,
                )
                values.append(
                    {
                        "game_id": gid,
                        "current_players": row.current_players,
                        "players_24h_delta": row.players_24h_delta,
                        "players_7d_spark": row.players_7d_spark,
                        "review_count": row.review_count,
                        "twitch_viewers": row.twitch_viewers,
                        "last_signal_at": row.last_signal_at,
                        "updated_at": now,
                    }
                )

            stmt = pg_insert(GameStats).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[GameStats.game_id],
                set_={
                    "current_players": stmt.excluded.current_players,
                    "players_24h_delta": stmt.excluded.players_24h_delta,
                    "players_7d_spark": stmt.excluded.players_7d_spark,
                    "review_count": stmt.excluded.review_count,
                    "twitch_viewers": stmt.excluded.twitch_viewers,
                    "last_signal_at": stmt.excluded.last_signal_at,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            await session.execute(stmt)
        total += len(values)

    log.info("stats_refresh_done", games=total)
    return total
