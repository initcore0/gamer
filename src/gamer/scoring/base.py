"""The scoring contract — the M3 delegation boundary (PLAN.md §4.5).

v1 is a **transparent weighted score**, not ML. The recommender is a set of
independent, named :class:`ScoreComponent`s (momentum, hype, watchability,
freshness, fit) plus :class:`Penalty`s (recently streamed, cooldown, blocklisted
genres). Each component returns a normalized contribution AND a human-readable
reason, so every recommendation carries a full breakdown → "why this game".

Contract shape (so component agents and the assembler build independently):

    component.score(candidate, ctx) -> ComponentScore   # value in ~[0,1], reason
    penalty.apply(candidate, ctx)   -> PenaltyResult     # multiplier/-delta, reason

The assembler combines weighted component values, applies penalties, and emits a
:class:`ScoredRecommendation` whose ``breakdown`` is persisted to
``recommendations.breakdown`` (jsonb) and rendered by ``/why``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Candidate:
    """A game under consideration, with the identifiers components need to fetch
    their own features. Components pull time-series / embeddings themselves (via
    the DB) keyed off ``game_id`` — the assembler does not pre-join everything.
    """

    game_id: int
    platform_app_id: int
    name: str
    genres: list[str] = field(default_factory=list)
    release_date: datetime | None = None


@dataclass(slots=True)
class ScoringContext:
    """Shared, request-level inputs. ``now`` is injected for deterministic tests
    and backtests (replay a past instant). ``prefs`` mirrors ``streamer_prefs``.
    """

    now: datetime
    liked_genres: list[str] = field(default_factory=list)
    blocked_genres: list[str] = field(default_factory=list)
    muted_game_ids: set[int] = field(default_factory=set)
    #: game_id -> last time we recommended/streamed it (for cooldown penalty).
    last_recommended: dict[int, datetime] = field(default_factory=dict)
    #: streamer taste vector (pgvector) for the fit component; None until learned.
    profile_embedding: list[float] | None = None


@dataclass(slots=True)
class ComponentScore:
    """One component's contribution.

    ``value`` is a normalized signal, conventionally in ``[0, 1]`` (the assembler
    applies the weight). ``reason`` is a short human string for the breakdown.
    ``detail`` carries structured numbers (slope, z-score…) for explainability.
    """

    value: float
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PenaltyResult:
    """A penalty's effect. ``multiplier`` in ``[0, 1]`` scales the score down
    (1.0 = no effect, 0.0 = fully suppressed). ``reason`` explains it.
    """

    multiplier: float
    reason: str
    applied: bool = False


@runtime_checkable
class ScoreComponent(Protocol):
    """A named, weighted contributor to the score."""

    #: stable key used in the breakdown jsonb and weight config.
    key: str

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore: ...


@runtime_checkable
class Penalty(Protocol):
    """A multiplicative down-weight (cooldown, blocklist, recently streamed)."""

    key: str

    async def apply(self, candidate: Candidate, ctx: ScoringContext) -> PenaltyResult: ...


@dataclass(slots=True)
class ScoredRecommendation:
    """The assembler's output for one candidate — persisted + rendered by /why."""

    game_id: int
    name: str
    score: float
    #: component key -> {weight, value, weighted, reason, detail} and penalties.
    breakdown: dict[str, Any] = field(default_factory=dict)

    def why(self) -> str:
        """Render a compact human explanation from the breakdown."""
        lines = [f"Score {self.score:.2f} for {self.name}:"]
        for key, part in self.breakdown.items():
            if not isinstance(part, dict):
                continue
            reason = part.get("reason", "")
            weighted = part.get("weighted")
            if weighted is not None:
                lines.append(f"  • {key}: {weighted:+.2f} — {reason}")
            else:
                lines.append(f"  • {key}: {reason}")
        return "\n".join(lines)
