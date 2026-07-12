"""The ``fit`` score component (PLAN.md §4.5).

``fit`` measures how well a candidate game matches the streamer's learned taste.
It embeds the game (name + genres) and compares that vector to the streamer's
profile vector (``streamer_prefs.profile_embedding``) via cosine similarity.

The profile vector is the feedback→fit loop: every game the streamer 👍'd or
marked "played" contributes its embedding, averaged into the taste vector by
:func:`build_profile_embedding`. Until the streamer has given any feedback the
profile is ``None`` and ``fit`` contributes nothing.
"""

from __future__ import annotations

from gamer.enrichment.embeddings import (
    Embedder,
    average_embeddings,
    cosine_similarity,
    game_text,
    get_embedder,
)
from gamer.scoring.base import Candidate, ComponentScore, ScoringContext


class FitComponent:
    """Cosine similarity of the candidate to the streamer's taste vector.

    ``value`` is in ``[0, 1]``: raw cosine similarity in ``[-1, 1]`` is clamped at
    0 (a negatively-correlated game is simply "no fit", not a penalty — penalties
    are a separate contract). When there is no profile yet, ``value`` is 0.0.
    """

    key = "fit"

    def __init__(self, embedder: Embedder | None = None) -> None:
        # Injectable so tests pass a deterministic HashEmbedder; falls back to the
        # configured embedder (LocalEmbedder when enabled, else HashEmbedder).
        self._embedder = embedder

    def _embed_candidate(self, candidate: Candidate) -> list[float]:
        embedder = self._embedder or get_embedder()
        return embedder.embed([game_text(candidate.name, candidate.genres)])[0]

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        if ctx.profile_embedding is None:
            return ComponentScore(value=0.0, reason="no taste profile yet", detail={"cosine": None})

        game_vec = self._embed_candidate(candidate)
        cosine = cosine_similarity(game_vec, ctx.profile_embedding)
        # Fold [-1, 1] similarity into a [0, 1] contribution.
        value = max(0.0, cosine)

        if value >= 0.6:
            reason = "strong match to your taste"
        elif value >= 0.3:
            reason = "some overlap with your taste"
        else:
            reason = "little overlap with your taste"

        return ComponentScore(value=value, reason=reason, detail={"cosine": round(cosine, 4)})


def build_profile_embedding(liked_game_embeddings: list[list[float]]) -> list[float] | None:
    """Average the embeddings of games the streamer liked/played into a taste
    vector (the feedback→fit loop). ``None`` when there is no feedback yet.

    Pure given the embeddings; the DB-reading wrapper that gathers
    ``Feedback``→``Recommendation``→``Game`` embeddings is integration-only.
    """
    return average_embeddings(liked_game_embeddings)


async def compute_profile_embedding(
    *,
    key: str = "default",
    embedder: Embedder | None = None,
) -> list[float] | None:
    """Read the games profile ``key`` 👍'd / played and build its taste vector.

    Joins ``Feedback`` (verdict UP/PLAYED) → ``Recommendation`` → ``Game`` and
    averages each game's embedding (name + genres). Scoped per profile
    (multi-user): only feedback on recommendations *owned by* ``key`` (via
    ``Recommendation.pref_key``) contributes, so each user's taste is learned from
    their own thumbs-up alone. Talks to the database → integration-only. Returns
    ``None`` when that profile has no positive feedback yet.
    """
    from sqlalchemy import select

    from gamer.db.engine import session_scope
    from gamer.db.models import Feedback, FeedbackVerdict, Game, Recommendation

    emb = embedder or get_embedder()
    async with session_scope() as session:
        stmt = (
            select(Game.name, Game.genres)
            .join(Recommendation, Recommendation.game_id == Game.id)
            .join(Feedback, Feedback.rec_id == Recommendation.id)
            .where(Recommendation.pref_key == key)
            .where(Feedback.verdict.in_((FeedbackVerdict.UP, FeedbackVerdict.PLAYED)))
        )
        rows = (await session.execute(stmt)).all()

    if not rows:
        return None
    vectors = emb.embed([game_text(name, list(genres or [])) for name, genres in rows])
    return build_profile_embedding(vectors)


async def update_profile_embedding(
    *,
    key: str = "default",
    embedder: Embedder | None = None,
) -> list[float] | None:
    """Recompute profile ``key``'s taste vector from its feedback and persist it.

    The feedback→fit loop, scoped per profile (multi-user): reads the games this
    profile thumbed-up/played (:func:`compute_profile_embedding`) and writes the
    averaged vector onto that profile's ``StreamerPref.profile_embedding``. A
    no-op (leaving the row untouched) when the profile has no positive feedback
    yet. Creates the prefs row on demand so a brand-new profile that gives
    feedback gets a taste vector. Integration-only (talks to the DB). Returns the
    new vector, or ``None`` when there was nothing to compute.
    """
    from sqlalchemy import select

    from gamer.db.engine import session_scope
    from gamer.db.models import StreamerPref

    vector = await compute_profile_embedding(key=key, embedder=embedder)
    if vector is None:
        return None
    async with session_scope() as session:
        prefs = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == key))
        ).scalar_one_or_none()
        if prefs is None:
            prefs = StreamerPref(key=key)
            session.add(prefs)
        prefs.profile_embedding = vector
    return vector
