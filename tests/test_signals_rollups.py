"""Unit tests for the rollup-writer pure helper (UI_PLAN.md §5.5, UI-M3).

DB-free: only :func:`recompute_floor` is exercised here. The set-based upsert
(:func:`refresh_rollups`) is covered under ``@integration``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from gamer.signals.rollups import recompute_floor


def test_recompute_floor_none_means_full_history() -> None:
    assert recompute_floor(None) is None


def test_recompute_floor_returns_newest_bucket() -> None:
    bucket = datetime(2026, 7, 8, tzinfo=UTC)
    assert recompute_floor(bucket) == bucket
