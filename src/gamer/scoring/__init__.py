"""Transparent weighted recommender with score breakdowns (M3, PLAN.md §4.5)."""

from __future__ import annotations

from gamer.scoring.assembler import DEFAULT_WEIGHTS, Assembler
from gamer.scoring.base import (
    Candidate,
    ComponentScore,
    Penalty,
    PenaltyResult,
    ScoreComponent,
    ScoredRecommendation,
    ScoringContext,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "Assembler",
    "Candidate",
    "ComponentScore",
    "Penalty",
    "PenaltyResult",
    "ScoreComponent",
    "ScoredRecommendation",
    "ScoringContext",
]
