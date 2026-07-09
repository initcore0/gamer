"""DB-free unit tests for the signal-derived score components.

Every test here exercises the pure math helpers or the pure ``_evaluate``
methods with plain numbers/timestamps — no database, no session. End-to-end
``.score()`` coverage against a real DB would be marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gamer.scoring.components.signals import (
    FRESHNESS_HALFLIFE_DAYS,
    HYPE_NEWS_SATURATION,
    MOMENTUM_MIN_SAMPLES,
    FreshnessComponent,
    HypeComponent,
    MomentumComponent,
    exp_decay,
    logistic,
    saturating,
    slope,
    zscore,
)

NOW = datetime(2026, 7, 9, tzinfo=UTC)
WINDOW_START = NOW - timedelta(days=7)


# ── slope ─────────────────────────────────────────────────────────────────────


def test_slope_rising_series_positive() -> None:
    assert slope([0.0, 1.0, 2.0, 3.0], [0.0, 10.0, 20.0, 30.0]) == 10.0


def test_slope_falling_series_negative() -> None:
    assert slope([0.0, 1.0, 2.0], [30.0, 20.0, 10.0]) == -10.0


def test_slope_flat_series_zero() -> None:
    assert slope([0.0, 1.0, 2.0, 3.0], [5.0, 5.0, 5.0, 5.0]) == 0.0


def test_slope_single_point_zero() -> None:
    assert slope([1.0], [42.0]) == 0.0


def test_slope_no_x_variance_zero() -> None:
    assert slope([2.0, 2.0, 2.0], [1.0, 2.0, 3.0]) == 0.0


# ── zscore ────────────────────────────────────────────────────────────────────


def test_zscore_above_mean_positive() -> None:
    z = zscore(10.0, [2.0, 4.0, 6.0])
    assert z > 0


def test_zscore_at_mean_zero() -> None:
    assert zscore(4.0, [2.0, 4.0, 6.0]) == 0.0


def test_zscore_zero_variance_returns_zero() -> None:
    assert zscore(5.0, [3.0, 3.0, 3.0]) == 0.0


def test_zscore_single_sample_zero() -> None:
    assert zscore(5.0, [3.0]) == 0.0


# ── logistic ──────────────────────────────────────────────────────────────────


def test_logistic_zero_is_half() -> None:
    assert logistic(0.0) == 0.5


def test_logistic_monotonic_and_bounded() -> None:
    assert 0.0 < logistic(-10.0) < 0.5 < logistic(10.0) < 1.0


def test_logistic_extreme_no_overflow() -> None:
    assert logistic(1e6) <= 1.0
    assert logistic(-1e6) >= 0.0


# ── saturating ────────────────────────────────────────────────────────────────


def test_saturating_at_saturation_is_half() -> None:
    assert saturating(4.0, 4.0) == 0.5


def test_saturating_zero_and_negative() -> None:
    assert saturating(0.0, 4.0) == 0.0
    assert saturating(-3.0, 4.0) == 0.0


def test_saturating_large_approaches_one() -> None:
    assert saturating(1000.0, 4.0) > 0.99


# ── exp_decay ─────────────────────────────────────────────────────────────────


def test_exp_decay_zero_age_is_one() -> None:
    assert exp_decay(0.0, 30.0) == 1.0


def test_exp_decay_future_reference_is_one() -> None:
    assert exp_decay(-5.0, 30.0) == 1.0


def test_exp_decay_halflife_is_one_over_e() -> None:
    assert abs(exp_decay(30.0, 30.0) - (1.0 / 2.718281828459045)) < 1e-9


def test_exp_decay_old_is_small() -> None:
    assert exp_decay(365.0, 30.0) < 0.01


# ── MomentumComponent._evaluate ───────────────────────────────────────────────


def _players(values: list[float]) -> list[tuple[datetime, float]]:
    return [(WINDOW_START + timedelta(days=i), v) for i, v in enumerate(values)]


def test_momentum_rising_series_high() -> None:
    cs = MomentumComponent()._evaluate(_players([100, 200, 400, 800, 1600]), WINDOW_START)
    assert cs.value > 0.5
    assert cs.detail["slope"] > 0
    assert cs.detail["zscore"] > 0
    assert cs.detail["latest"] == 1600


def test_momentum_flat_series_near_half() -> None:
    cs = MomentumComponent()._evaluate(_players([500, 500, 500, 500]), WINDOW_START)
    # flat → z-score 0 → logistic(0) == 0.5
    assert cs.value == 0.5
    assert cs.detail["slope"] == 0.0
    assert cs.detail["zscore"] == 0.0


def test_momentum_falling_series_low() -> None:
    cs = MomentumComponent()._evaluate(_players([1600, 800, 400, 200, 100]), WINDOW_START)
    assert cs.value < 0.5
    assert cs.detail["slope"] < 0


def test_momentum_insufficient_samples() -> None:
    few = _players([100.0] * (MOMENTUM_MIN_SAMPLES - 1))
    cs = MomentumComponent()._evaluate(few, WINDOW_START)
    assert cs.value == 0.0
    assert cs.reason == "insufficient data"
    assert cs.detail["latest"] is None


def test_momentum_single_sample_insufficient() -> None:
    cs = MomentumComponent()._evaluate(_players([100.0]), WINDOW_START)
    assert cs.value == 0.0
    assert cs.reason == "insufficient data"


# ── HypeComponent._evaluate ───────────────────────────────────────────────────


def test_hype_news_burst_high() -> None:
    cs = HypeComponent()._evaluate(int(HYPE_NEWS_SATURATION * 4), [])
    assert cs.value > 0.4
    assert cs.detail["news_count"] == int(HYPE_NEWS_SATURATION * 4)


def test_hype_no_activity_zero() -> None:
    cs = HypeComponent()._evaluate(0, [])
    assert cs.value == 0.0
    assert cs.reason == "no recent news or reviews"


def test_hype_review_delta_contributes() -> None:
    cs = HypeComponent()._evaluate(0, [1000.0, 1600.0])
    assert cs.value > 0.0
    assert cs.detail["review_delta"] == 600.0


def test_hype_review_decrease_clamped_to_zero() -> None:
    cs = HypeComponent()._evaluate(0, [1600.0, 1000.0])
    assert cs.detail["review_delta"] == 0.0
    assert cs.value == 0.0


def test_hype_news_beats_no_news() -> None:
    hot = HypeComponent()._evaluate(6, [])
    cold = HypeComponent()._evaluate(1, [])
    assert hot.value > cold.value


# ── FreshnessComponent._evaluate ──────────────────────────────────────────────


def test_freshness_just_released_high() -> None:
    cs = FreshnessComponent()._evaluate(NOW, NOW - timedelta(hours=6), None)
    assert cs.value > 0.99
    assert cs.detail["source"] == "release"


def test_freshness_old_release_low() -> None:
    cs = FreshnessComponent()._evaluate(NOW, NOW - timedelta(days=365), None)
    assert cs.value < 0.01
    assert cs.detail["source"] == "release"


def test_freshness_uses_most_recent_of_release_and_news() -> None:
    old_release = NOW - timedelta(days=200)
    recent_news = NOW - timedelta(days=2)
    cs = FreshnessComponent()._evaluate(NOW, old_release, recent_news)
    assert cs.detail["source"] == "news"
    assert cs.value > exp_decay(200.0, FRESHNESS_HALFLIFE_DAYS)


def test_freshness_no_dates_zero() -> None:
    cs = FreshnessComponent()._evaluate(NOW, None, None)
    assert cs.value == 0.0
    assert cs.reason == "no release or news date"
    assert cs.detail["age_days"] is None


def test_freshness_halflife_boundary() -> None:
    cs = FreshnessComponent()._evaluate(NOW, NOW - timedelta(days=FRESHNESS_HALFLIFE_DAYS), None)
    assert abs(cs.value - (1.0 / 2.718281828459045)) < 1e-6
