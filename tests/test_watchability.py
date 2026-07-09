"""DB-free unit tests for the watchability score component.

These exercise the pure ratio helper and the pure ``_evaluate`` method with plain
numbers — no database, no session. End-to-end ``.score()`` coverage against a real
DB would be marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

from gamer.scoring.components.watchability import (
    WATCHABILITY_RATIO_SATURATION,
    WatchabilityComponent,
    watchability_value,
)

# ── watchability_value (pure ratio → [0, 1]) ──────────────────────────────────


def test_value_bounded_in_unit_interval() -> None:
    for viewers, players in [(0, 0), (100, 1), (1, 1000), (1_000_000, 1), (50, 200)]:
        v = watchability_value(float(viewers), float(players))
        assert 0.0 <= v <= 1.0


def test_no_viewers_is_zero() -> None:
    assert watchability_value(0.0, 1000.0) == 0.0
    assert watchability_value(0.0, 0.0) == 0.0


def test_high_viewers_low_players_is_high() -> None:
    # 10k viewers, 100 players → ratio 100 → saturates near 1.0.
    v = watchability_value(10_000.0, 100.0)
    assert v > 0.9


def test_low_viewers_high_players_is_low() -> None:
    # 10 viewers, 100k players → tiny ratio → near 0.
    v = watchability_value(10.0, 100_000.0)
    assert v < 0.01


def test_ratio_at_saturation_is_half() -> None:
    # ratio == saturation → 0.5. Pick viewers/players hitting that ratio exactly.
    players = 1000.0
    viewers = WATCHABILITY_RATIO_SATURATION * players
    assert abs(watchability_value(viewers, players) - 0.5) < 1e-9


def test_monotonic_in_viewers() -> None:
    lo = watchability_value(100.0, 1000.0)
    hi = watchability_value(500.0, 1000.0)
    assert hi > lo


def test_zero_players_uses_floor_of_one() -> None:
    # players clamped to 1, so ratio == viewers; big viewers → high.
    assert watchability_value(1000.0, 0.0) > 0.9


# ── WatchabilityComponent._evaluate ───────────────────────────────────────────


def test_evaluate_no_twitch_data_zero() -> None:
    cs = WatchabilityComponent()._evaluate(None, 500.0)
    assert cs.value == 0.0
    assert cs.reason == "no Twitch data"
    assert cs.detail["viewers"] is None


def test_evaluate_high_ratio_high_value() -> None:
    cs = WatchabilityComponent()._evaluate(10_000.0, 100.0)
    assert cs.value > 0.9
    assert cs.detail["viewers"] == 10_000.0
    assert cs.detail["players"] == 100.0
    assert cs.detail["ratio"] > 1.0


def test_evaluate_low_ratio_low_value() -> None:
    cs = WatchabilityComponent()._evaluate(10.0, 100_000.0)
    assert cs.value < 0.01


def test_evaluate_missing_players_treated_as_no_baseline() -> None:
    cs = WatchabilityComponent()._evaluate(500.0, None)
    assert cs.value > 0.0
    assert cs.detail["players"] == 0.0
    assert "no player baseline" in cs.reason


def test_component_key() -> None:
    assert WatchabilityComponent().key == "watchability"
