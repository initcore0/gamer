"""Scorer service — the runtime that turns tracked games into ranked, persisted
recommendations (PLAN.md §4.5, M3).

Flow:
  1. Load candidate games (``tracked``) from the DB.
  2. Build a :class:`~gamer.scoring.base.ScoringContext` from the streamer's
     :class:`~gamer.db.models.StreamerPref` (liked/blocked genres, muted ids,
     profile embedding) and recent :class:`~gamer.db.models.Recommendation`
     history (``last_recommended`` for the cooldown penalty).
  3. Assemble an :class:`~gamer.scoring.assembler.Assembler` from whatever
     components discovery found plus the local penalties, and rank.
  4. Persist the top results as ``Recommendation`` rows (score + breakdown jsonb,
     ``sent_at`` left null until the notifier sends them).

Components are discovered resiliently (see :mod:`gamer.scoring.registry`) so the
service runs even before every component module lands.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gamer.db import session_scope
from gamer.db.models import Game, Recommendation, StreamerPref
from gamer.logging import get_logger
from gamer.notify.digest import apply_genre_quota
from gamer.scoring.assembler import DEFAULT_WEIGHTS, Assembler
from gamer.scoring.base import (
    Candidate,
    Penalty,
    ScoreComponent,
    ScoredRecommendation,
    ScoringContext,
)
from gamer.scoring.penalties import BlocklistPenalty, CooldownPenalty
from gamer.scoring.registry import discover_components

log = get_logger("scoring.service")


def default_penalties() -> list[Penalty]:
    """The penalties applied by the default recommender."""
    return [CooldownPenalty(), BlocklistPenalty()]


def build_assembler(
    components: Sequence[ScoreComponent] | None = None,
    penalties: Sequence[Penalty] | None = None,
) -> Assembler:
    """Construct an :class:`Assembler` from discovered components + penalties.

    Both arguments are injectable for tests / backtests; when omitted, components
    are discovered from :mod:`gamer.scoring.components` and the default penalties
    are used.
    """
    comps = list(components) if components is not None else discover_components()
    pens = list(penalties) if penalties is not None else default_penalties()
    log.info(
        "assembler_built",
        components=[c.key for c in comps],
        penalties=[p.key for p in pens],
    )
    return Assembler(components=comps, penalties=pens, weights=dict(DEFAULT_WEIGHTS))


async def load_candidates(session: AsyncSession) -> list[Candidate]:
    """Load tracked games as scoring candidates.

    Components pull their own time-series/embeddings keyed off ``game_id``, so we
    only need the game's identity + genres + release date here.
    """
    rows = (
        await session.execute(
            select(
                Game.id,
                Game.platform_app_id,
                Game.name,
                Game.genres,
                Game.release_date,
            ).where(Game.tracked.is_(True))
        )
    ).all()
    return [
        Candidate(
            game_id=gid,
            platform_app_id=app_id,
            name=name,
            genres=list(genres or []),
            release_date=release_date,
        )
        for gid, app_id, name, genres, release_date in rows
    ]


async def build_context(
    session: AsyncSession,
    *,
    now: datetime,
    key: str = "default",
) -> ScoringContext:
    """Build the request context from prefs + recent recommendation history."""
    prefs = (
        await session.execute(select(StreamerPref).where(StreamerPref.key == key))
    ).scalar_one_or_none()

    liked = list(prefs.liked_genres or []) if prefs else []
    blocked = list(prefs.blocked_genres or []) if prefs else []
    subscribed = list(prefs.subscribed_genres or []) if prefs else []
    muted = set(prefs.muted_game_ids or []) if prefs else set()
    embedding: list[float] | None = None
    if prefs is not None and prefs.profile_embedding is not None:
        embedding = list(prefs.profile_embedding)

    # last_recommended: most recent recommendation timestamp per game (<= now),
    # scoped to this profile (multi-user): user A's recent picks must not damp
    # user B's cooldown. Legacy rows carry ``pref_key='default'``.
    rec_rows = (
        await session.execute(
            select(Recommendation.game_id, Recommendation.created_at)
            .where(Recommendation.created_at <= now)
            .where(Recommendation.pref_key == key)
            .order_by(Recommendation.game_id, Recommendation.created_at.desc())
        )
    ).all()
    last_recommended: dict[int, datetime] = {}
    for game_id, created_at in rec_rows:
        if game_id not in last_recommended:
            last_recommended[game_id] = created_at

    return ScoringContext(
        now=now,
        liked_genres=liked,
        blocked_genres=blocked,
        subscribed_genres=subscribed,
        muted_game_ids=muted,
        last_recommended=last_recommended,
        profile_embedding=embedding,
    )


async def _persist(
    session: AsyncSession, ranked: Sequence[ScoredRecommendation], *, key: str
) -> None:
    """Persist ranked results as ``Recommendation`` rows (unsent), owned by ``key``."""
    for r in ranked:
        session.add(
            Recommendation(
                game_id=r.game_id,
                pref_key=key,
                score=r.score,
                breakdown=r.breakdown,
                sent_at=None,
            )
        )


async def recommend(
    limit: int = 5,
    key: str = "default",
    *,
    now: datetime | None = None,
    persist: bool = True,
    subscribed_quota: int = 0,
) -> list[ScoredRecommendation]:
    """Score tracked games and return the top ``limit`` recommendations.

    Persists the *final displayed* results as ``Recommendation`` rows unless
    ``persist`` is False. ``now`` is injectable (defaults to the current UTC
    instant) so the same path drives the backtest harness.

    ``subscribed_quota`` (M7): when > 0, ranks *all* candidates, then applies the
    pure :func:`~gamer.notify.digest.apply_genre_quota` over the full ranked pool so
    at least ``min(subscribed_quota, available)`` of the returned picks come from
    subscribed genres — promoting subscribed-genre games from below the top-``limit``
    cut when needed. Only the final ``limit`` picks are persisted (so promotion never
    pollutes the cooldown history with games that weren't shown). ``subscribed_quota
    == 0`` keeps the pre-M7 behavior byte-identical.
    """
    now = now or datetime.now(UTC)
    assembler = build_assembler()
    async with session_scope() as session:
        candidates = await load_candidates(session)
        ctx = await build_context(session, now=now, key=key)
        if subscribed_quota > 0:
            full = await assembler.rank(candidates, ctx, limit=None)
            ranked = apply_genre_quota(
                full, ctx.subscribed_genres, limit=limit, slots=subscribed_quota
            )
        else:
            ranked = await assembler.rank(candidates, ctx, limit=limit)
        if persist and ranked:
            await _persist(session, ranked, key=key)
    log.info("recommend", key=key, limit=limit, returned=len(ranked), persisted=persist)
    return ranked
