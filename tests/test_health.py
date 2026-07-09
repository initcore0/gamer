"""Stale-source health logic — pure, DB-free unit tests (PLAN.md §6 M4).

``stale_sources_from_rows`` is the injectable core of ``find_stale_sources``:
given a fixed ``now`` and a list of :class:`SourceCursor` rows it decides which
sources are stale. No clock, no database. The DB-backed ``find_stale_sources`` /
``alert_stale_sources_once`` are exercised behind ``@pytest.mark.integration``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gamer.db.models import SourceCursor
from gamer.health import stale_dedup_key, stale_sources_from_rows

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)


def _cursor(
    source: str,
    *,
    last_run: datetime | None = None,
    last_success: datetime | None = None,
) -> SourceCursor:
    return SourceCursor(
        source=source,
        cursor={},
        last_run_at=last_run,
        last_success_at=last_success,
    )


def test_silent_beyond_threshold_is_stale() -> None:
    rows = [_cursor("steam_api", last_run=NOW, last_success=NOW - timedelta(hours=25))]
    assert stale_sources_from_rows(rows, now=NOW, stale_after_hours=24) == ["steam_api"]


def test_fresh_success_is_not_stale() -> None:
    rows = [_cursor("steam_api", last_run=NOW, last_success=NOW - timedelta(hours=1))]
    assert stale_sources_from_rows(rows, now=NOW, stale_after_hours=24) == []


def test_exactly_at_threshold_is_not_stale() -> None:
    # last_success == threshold boundary → not older than, so not stale.
    rows = [_cursor("steam_api", last_run=NOW, last_success=NOW - timedelta(hours=24))]
    assert stale_sources_from_rows(rows, now=NOW, stale_after_hours=24) == []


def test_never_run_is_ignored() -> None:
    rows = [_cursor("rss", last_run=None, last_success=None)]
    assert stale_sources_from_rows(rows, now=NOW, stale_after_hours=24) == []


def test_ran_but_never_succeeded_is_stale() -> None:
    rows = [_cursor("rss", last_run=NOW, last_success=None)]
    assert stale_sources_from_rows(rows, now=NOW, stale_after_hours=24) == ["rss"]


def test_mixed_rows_return_sorted_stale_only() -> None:
    rows = [
        _cursor("steam_store", last_run=NOW, last_success=NOW - timedelta(hours=30)),
        _cursor("steam_api", last_run=NOW, last_success=NOW - timedelta(minutes=5)),
        _cursor("rss", last_run=NOW, last_success=None),
        _cursor("twitch", last_run=None, last_success=None),
    ]
    assert stale_sources_from_rows(rows, now=NOW, stale_after_hours=24) == ["rss", "steam_store"]


def test_dedup_key_format() -> None:
    assert stale_dedup_key("steam_api", NOW) == "stale:steam_api:2026-07-09"
    # Same source, same day → same key (deduped); different day → different key.
    later_same_day = NOW + timedelta(hours=6)
    assert stale_dedup_key("steam_api", later_same_day) == "stale:steam_api:2026-07-09"
    next_day = NOW + timedelta(days=1)
    assert stale_dedup_key("steam_api", next_day) == "stale:steam_api:2026-07-10"
