"""Unit tests for the DB-free stats-computation helpers (UI_PLAN.md §5.4).

The bucketing/downsampling and delta logic are pure functions — no DB, no clock
dependency beyond the injected ``now`` — so they are exhaustively unit-tested
here. The DB upsert pass lives in ``test_signals_stats_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gamer.signals.stats import (
    MAX_SPARK_POINTS,
    Sample,
    build_stats_row,
    compute_delta,
    downsample_spark,
)

NOW = datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC)


def _s(hours_ago: float, value: float) -> Sample:
    return Sample(ts=NOW - timedelta(hours=hours_ago), value=value)


# ── downsample_spark ─────────────────────────────────────────────────────────


def test_spark_empty_is_empty_list() -> None:
    assert downsample_spark([], now=NOW) == []


def test_spark_single_point() -> None:
    assert downsample_spark([_s(1, 42.0)], now=NOW) == [42.0]


def test_spark_averages_within_bucket() -> None:
    # Two samples in the same 8h bucket → their mean, one point.
    out = downsample_spark([_s(1, 10.0), _s(2, 20.0)], now=NOW)
    assert out == [15.0]


def test_spark_skips_empty_buckets_and_orders_oldest_first() -> None:
    # One sample near the start of the window, one near the end.
    out = downsample_spark([_s(167, 5.0), _s(1, 9.0)], now=NOW)
    assert out == [5.0, 9.0]  # oldest → newest, gap collapsed


def test_spark_caps_at_max_points() -> None:
    # One sample in every hour of the 7-day window → ≤21 buckets.
    samples = [_s(h, float(h)) for h in range(1, 24 * 7)]
    out = downsample_spark(samples, now=NOW)
    assert 0 < len(out) <= MAX_SPARK_POINTS


def test_spark_ignores_out_of_window() -> None:
    out = downsample_spark([_s(24 * 8, 1.0), _s(1, 2.0)], now=NOW)
    assert out == [2.0]


def test_spark_constant_series() -> None:
    samples = [_s(h, 100.0) for h in (1, 20, 40, 60)]
    out = downsample_spark(samples, now=NOW)
    assert set(out) == {100.0}


# ── compute_delta ────────────────────────────────────────────────────────────


def test_delta_none_when_empty() -> None:
    assert compute_delta([], now=NOW) is None


def test_delta_none_without_baseline() -> None:
    # Only recent samples (< 24h old) → no baseline.
    assert compute_delta([_s(1, 10.0), _s(2, 20.0)], now=NOW) is None


def test_delta_latest_minus_baseline() -> None:
    # latest = 30 (1h ago), baseline = latest sample ≥24h old = 10 (25h ago).
    samples = [_s(1, 30.0), _s(25, 10.0), _s(48, 5.0)]
    assert compute_delta(samples, now=NOW) == 20.0


def test_delta_picks_latest_old_enough_baseline() -> None:
    # Among samples ≥24h old, the most recent (25h ago, value 10) is the baseline.
    samples = [_s(1, 30.0), _s(25, 10.0), _s(100, 1.0)]
    assert compute_delta(samples, now=NOW) == 20.0


def test_delta_can_be_negative() -> None:
    samples = [_s(1, 5.0), _s(30, 20.0)]
    assert compute_delta(samples, now=NOW) == -15.0


# ── build_stats_row ──────────────────────────────────────────────────────────


def test_build_stats_row_full() -> None:
    players = [_s(1, 30.0), _s(25, 10.0)]
    reviews = [_s(2, 500.0), _s(50, 400.0)]
    twitch = [_s(3, 12.0)]
    row = build_stats_row(
        players=players,
        reviews=reviews,
        twitch=twitch,
        last_signal_at=NOW - timedelta(hours=1),
        now=NOW,
    )
    assert row.current_players == 30.0
    assert row.players_24h_delta == 20.0
    assert row.review_count == 500.0
    assert row.twitch_viewers == 12.0
    assert row.players_7d_spark  # non-empty
    assert row.last_signal_at == NOW - timedelta(hours=1)


def test_build_stats_row_no_data() -> None:
    row = build_stats_row(players=[], reviews=[], twitch=[], last_signal_at=None, now=NOW)
    assert row.current_players is None
    assert row.players_24h_delta is None
    assert row.players_7d_spark == []
    assert row.review_count is None
    assert row.twitch_viewers is None
    assert row.last_signal_at is None
