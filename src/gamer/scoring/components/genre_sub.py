"""The ``genre_sub`` score component (GENRE_SUBS_PLAN.md, M7).

A genre *subscription* is a hard commitment: candidates whose genres intersect the
streamer's ``subscribed_genres`` (case-insensitively) get a strong, explainable
boost. Unlike ``fit`` (a soft, learned taste vector) this is a deterministic,
DB-free flag comparison.

Value is 1.0 with reason ``"subscribed genre: <Matched>"`` on a match — reporting
the *candidate's* original casing — else 0.0.
"""

from __future__ import annotations

from gamer.scoring.base import Candidate, ComponentScore, ScoringContext


class GenreSubComponent:
    """1.0 when a candidate genre matches a subscribed genre; else 0.0.

    Pure — no DB, no I/O. Matching is case-insensitive; the reason reports the
    candidate's own casing so the explanation reads naturally.
    """

    key = "genre_sub"

    async def score(self, candidate: Candidate, ctx: ScoringContext) -> ComponentScore:
        subscribed = {g.lower() for g in ctx.subscribed_genres}
        if subscribed:
            for genre in candidate.genres:
                if genre.lower() in subscribed:
                    return ComponentScore(
                        value=1.0,
                        reason=f"subscribed genre: {genre}",
                        detail={"matched": genre},
                    )
        return ComponentScore(
            value=0.0,
            reason="not a subscribed genre",
            detail={"matched": None},
        )
