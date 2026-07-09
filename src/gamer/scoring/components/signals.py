"""Signal-derived score components: momentum, hype, freshness (PLAN.md §4.3/§4.5).

These three components turn raw time-series and news signals into normalized
``[0, 1]`` contributions with human-readable reasons:

* :class:`MomentumComponent` — 7-day slope + z-score of concurrent-player counts.
* :class:`HypeComponent` — recent news / review-count velocity.
* :class:`FreshnessComponent` — decay from a recent release or news update.

All of the math lives in small **pure** helper functions that take plain numbers
and timestamps (no DB, no I/O) so they are unit-testable in isolation. Each
component's async ``score()`` method fetches features from the DB keyed off
``candidate.game_id`` and delegates the arithmetic to those helpers.

Every time window and decay is measured relative to ``ctx.now`` (never
``datetime.now()``) so scoring is deterministic and replayable in backtests.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import func, select

from gamer.db import session_scope
from gamer.db.models import Game, NewsItem, SignalMetric, SignalSample
from gamer.logging import get_logger
from gamer.scoring.base import Candidate, ComponentScore, ScoringContext

log = get_logger("scoring.components.signals")

# ── Tunable constants ─────────────────────────────────────────────────────────

MOMENTUM_WINDOW = timedelta(days=7)
#: Minimum PLAYERS samples needed to fit a slope / baseline.
MOMENTUM_MIN_SAMPLES = 3
#: Logistic steepness for squashing the momentum z-score into [0, 1].
MOMENTUM_ZSCORE_STEEPNESS = 1.0

HYPE_WINDOW = timedelta(days=14)
#: News-item count that saturates the hype signal to ~1.0.
HYPE_NEWS_SATURATION = 8.0
#: Review-count delta that saturates the hype signal to ~1.0.
HYPE_REVIEW_SATURATION = 500.0
#: Relative weight of news velocity vs review velocity in the blended hype value.
HYPE_NEWS_WEIGHT = 0.6

#: Time (in days) over which freshness decays to 1/e (~0.37).
FRESHNESS_HALFLIFE_DAYS = 30.0


# ── Pure math helpers (DB-free, unit-testable) ────────────────────────────────


def slope(xs: list[float], ys: list[float]) -> float:
    """Ordinary-least-squares slope of ``ys`` against ``xs``.

    Returns 0.0 when there are fewer than two points or ``xs`` has no variance
    (a vertical fit is undefined, so we treat it as flat).
    """
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0.0:
        return 0.0
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return cov / var_x


def zscore(value: float, sample: list[float]) -> float:
    """Z-score of ``value`` against the mean/stddev (population) of ``sample``.

    Returns 0.0 when the sample has fewer than two points or zero variance —
    i.e. there is no baseline to deviate from.
    """
    n = len(sample)
    if n < 2:
        return 0.0
    mean = sum(sample) / n
    var = sum((s - mean) ** 2 for s in sample) / n
    if var <= 0.0:
        return 0.0
    return (value - mean) / math.sqrt(var)


def logistic(x: float, *, steepness: float = 1.0) -> float:
    """Logistic squash of a real number into ``(0, 1)``.

    ``x == 0`` maps to 0.5; large positive ``x`` → ~1.0, large negative → ~0.0.
    Clamps the exponent to avoid overflow for extreme inputs.
    """
    z = max(-60.0, min(60.0, steepness * x))
    return 1.0 / (1.0 + math.exp(-z))


def saturating(value: float, saturation: float) -> float:
    """Map a non-negative count onto ``[0, 1)`` with diminishing returns.

    Uses ``value / (value + saturation)`` so ``value == saturation`` → 0.5 and
    the curve approaches 1.0 asymptotically. Negative values clamp to 0.0.
    """
    if value <= 0.0 or saturation <= 0.0:
        return 0.0
    return value / (value + saturation)


def exp_decay(age_days: float, halflife_days: float) -> float:
    """Exponential recency decay in ``[0, 1]``.

    ``age_days <= 0`` (just released/updated, or a future date) → 1.0; the value
    decays toward 0 as ``age_days`` grows, reaching ``1/e`` at ``halflife_days``.
    """
    if age_days <= 0.0:
        return 1.0
    if halflife_days <= 0.0:
        return 0.0
    return math.exp(-age_days / halflife_days)


def _age_days(now: datetime, then: datetime) -> float:
    """Whole/fractional days between ``then`` and ``now`` (>= reference)."""
    return (now - then).total_seconds() / 86400.0


# ── Components ────────────────────────────────────────────────────────────────


class MomentumComponent:
    """Player-count growth: 7-day slope + z-score vs the game's own baseline.

    We fetch the game's ``PLAYERS`` samples inside the momentum window, fit an
    OLS slope over (seconds-since-window-start, players), and compute how far the
    latest sample sits above the window's mean in standard deviations. The
    z-score is logistically squashed into ``[0, 1]`` so a game trending up
    relative to its own recent baseline scores high, a flat/declining one low.
    """

    key = "momentum"

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        since = ctx.now - MOMENTUM_WINDOW
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(SignalSample.ts, SignalSample.value)
                    .where(SignalSample.game_id == candidate.game_id)
                    .where(SignalSample.metric == SignalMetric.PLAYERS)
                    .where(SignalSample.ts >= since)
                    .where(SignalSample.ts <= ctx.now)
                    .order_by(SignalSample.ts)
                )
            ).all()

        samples = [(ts, float(value)) for ts, value in rows]
        return self._evaluate(samples, since)

    def _evaluate(self, samples: list[tuple[datetime, float]], since: datetime) -> ComponentScore:
        """Pure evaluation of ordered ``(ts, players)`` samples → ComponentScore."""
        if len(samples) < MOMENTUM_MIN_SAMPLES:
            return ComponentScore(
                value=0.0,
                reason="insufficient data",
                detail={"slope": 0.0, "zscore": 0.0, "latest": None, "samples": len(samples)},
            )

        # x in days-since-window-start keeps the slope in interpretable units.
        xs = [_age_days(ts, since) for ts, _ in samples]
        ys = [v for _, v in samples]
        s = slope(xs, ys)
        latest = ys[-1]
        z = zscore(latest, ys)
        value = logistic(z, steepness=MOMENTUM_ZSCORE_STEEPNESS)

        if s > 0:
            reason = f"players trending up (+{s:.0f}/day, z={z:+.2f})"
        elif s < 0:
            reason = f"players trending down ({s:.0f}/day, z={z:+.2f})"
        else:
            reason = f"players flat (z={z:+.2f})"

        return ComponentScore(
            value=value,
            reason=reason,
            detail={
                "slope": round(s, 4),
                "zscore": round(z, 4),
                "latest": latest,
                "samples": len(samples),
            },
        )


class HypeComponent:
    """News / review velocity: more recent buzz → higher value.

    Blends two recent-velocity signals over the hype window: the count of news
    items published for the game, and the increase in cumulative
    ``REVIEW_COUNT``. Each is passed through a saturating curve (diminishing
    returns) and combined with a fixed weight into ``[0, 1]``.
    """

    key = "hype"

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        since = ctx.now - HYPE_WINDOW
        async with session_scope() as session:
            news_count = (
                await session.execute(
                    select(func.count())
                    .select_from(NewsItem)
                    .where(NewsItem.game_id == candidate.game_id)
                    .where(NewsItem.published_at >= since)
                    .where(NewsItem.published_at <= ctx.now)
                )
            ).scalar_one()

            review_rows = (
                await session.execute(
                    select(SignalSample.value)
                    .where(SignalSample.game_id == candidate.game_id)
                    .where(SignalSample.metric == SignalMetric.REVIEW_COUNT)
                    .where(SignalSample.ts >= since)
                    .where(SignalSample.ts <= ctx.now)
                    .order_by(SignalSample.ts)
                )
            ).all()

        review_values = [float(v) for (v,) in review_rows]
        return self._evaluate(int(news_count), review_values)

    def _evaluate(self, news_count: int, review_values: list[float]) -> ComponentScore:
        """Pure evaluation of news count + ordered review-count samples."""
        review_delta = 0.0
        if len(review_values) >= 2:
            review_delta = max(0.0, review_values[-1] - review_values[0])

        news_component = saturating(float(news_count), HYPE_NEWS_SATURATION)
        review_component = saturating(review_delta, HYPE_REVIEW_SATURATION)
        value = HYPE_NEWS_WEIGHT * news_component + (1.0 - HYPE_NEWS_WEIGHT) * review_component

        reason = (
            f"{news_count} news item(s), +{review_delta:.0f} reviews recently"
            if (news_count or review_delta)
            else "no recent news or reviews"
        )

        return ComponentScore(
            value=value,
            reason=reason,
            detail={
                "news_count": news_count,
                "review_delta": round(review_delta, 4),
                "news_component": round(news_component, 4),
                "review_component": round(review_component, 4),
            },
        )


class FreshnessComponent:
    """Recency of release or last major update, decaying from 1.0 toward 0.

    Takes the most recent of the game's ``release_date`` and its newest news
    item's ``published_at`` as the reference "freshness" instant, then applies an
    exponential decay against ``ctx.now``. A game released/updated today scores
    ~1.0; one untouched for months scores near 0.
    """

    key = "freshness"

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        async with session_scope() as session:
            release_date = (
                await session.execute(select(Game.release_date).where(Game.id == candidate.game_id))
            ).scalar_one_or_none()

            latest_news = (
                await session.execute(
                    select(func.max(NewsItem.published_at))
                    .where(NewsItem.game_id == candidate.game_id)
                    .where(NewsItem.published_at <= ctx.now)
                )
            ).scalar_one_or_none()

        return self._evaluate(ctx.now, release_date, latest_news)

    def _evaluate(
        self,
        now: datetime,
        release_date: datetime | None,
        latest_news: datetime | None,
    ) -> ComponentScore:
        """Pure decay of the most recent freshness instant against ``now``."""
        candidates = [d for d in (release_date, latest_news) if d is not None]
        if not candidates:
            return ComponentScore(
                value=0.0,
                reason="no release or news date",
                detail={"age_days": None, "source": None},
            )

        reference = max(candidates)
        source = "news" if reference is latest_news else "release"
        age = _age_days(now, reference)
        value = exp_decay(age, FRESHNESS_HALFLIFE_DAYS)

        if age <= 1.0:
            reason = f"just {source} (<1 day ago)"
        else:
            reason = f"{source} {age:.0f} days ago"

        return ComponentScore(
            value=value,
            reason=reason,
            detail={"age_days": round(age, 4), "source": source},
        )
