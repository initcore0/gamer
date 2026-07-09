"""Concrete score components (PLAN.md §4.5).

Each component implements :class:`~gamer.scoring.base.ScoreComponent`, fetching
its own features from the DB keyed off ``candidate.game_id`` and normalizing to
``[0, 1]``. The signal-derived trio lives in :mod:`signals`; the taste-based
``fit`` component lives in :mod:`fit`.
"""

from __future__ import annotations

from gamer.scoring.components.fit import FitComponent
from gamer.scoring.components.signals import (
    FreshnessComponent,
    HypeComponent,
    MomentumComponent,
)

__all__ = [
    "FitComponent",
    "FreshnessComponent",
    "HypeComponent",
    "MomentumComponent",
]
