"""Recommendation-feed JSON routes (API_CONTRACT.md §Recommendations).

* ``GET /api/v1/recommendations``          → the run-grouped feed for a profile.
* ``POST /api/v1/recommendations/refresh`` → run the scorer now for a profile.

All SQL lives in ``queries.recs``; this module only parses params and shapes the
JSON rows. No SQL here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from gamer.api.queries import recs as recs_q
from gamer.api.queries import users as users_q
from gamer.scoring.service import recommend

router = APIRouter()

# The digest's subscribed-genre quota, mirrored for the on-demand refresh so an
# API-triggered run matches what the daily digest would surface (M7).
_SUBSCRIBED_QUOTA = 3
# "all" is a sentinel meaning "every profile's rows" (no pref_key filter).
_ALL = "all"


def _rec_json(row: recs_q.RecRow) -> dict[str, Any]:
    """Shape one feed row into the contract's JSON (shared by GET and refresh)."""
    return {
        "id": row.id,
        "game_id": row.game_id,
        "game_name": row.game_name,
        "score": row.score,
        "user_key": row.user_key,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "sent_at": row.sent_at.isoformat() if row.sent_at else None,
        "feedback": row.feedback,
        "breakdown": row.breakdown,
    }


@router.get(
    "/api/v1/recommendations",
    tags=["recommendations"],
    summary="Recommendation feed (per profile, keyset-paginated)",
)
async def recommendations_json(
    user_key: str = Query(default="default"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=recs_q.DEFAULT_LIMIT, ge=1, le=100),
) -> dict[str, Any]:
    # ``all`` → every profile's rows (no filter); any other value filters to that
    # preference profile via ``Recommendation.pref_key``.
    key_filter = None if user_key == _ALL else user_key
    page = await recs_q.list_recommendations(cursor=cursor, limit=limit, user_key=key_filter)
    return {
        "recommendations": [_rec_json(row) for row in page.rows],
        "next_cursor": page.next_cursor,
    }


class RefreshBody(BaseModel):
    """Body for an on-demand recommender run (API_CONTRACT.md)."""

    user_key: str = Field(default="default", min_length=1)
    limit: int = Field(default=10, ge=1, le=20)


@router.post(
    "/api/v1/recommendations/refresh",
    tags=["recommendations"],
    summary="Run the recommender now for a profile and return the fresh picks",
)
async def recommendations_refresh(body: RefreshBody) -> dict[str, Any]:
    """Score ``body.user_key``'s profile now (persisting) and return fresh picks.

    Validates ``user_key`` against the known-profile allowlist first — an unknown
    (or ``"all"``) key is a 422, never a silent run against empty prefs. Then runs
    :func:`gamer.scoring.service.recommend` with the digest's ``subscribed_quota``
    so an API-triggered run matches the daily digest, and reads the just-persisted
    rows back through the shared feed query so the response is byte-identical in
    shape to the GET (feedback all zero, no ``next_cursor``).
    """
    user_key = body.user_key.strip()
    known = await users_q.profile_keys()
    if not user_key or user_key == _ALL or user_key not in known:
        raise HTTPException(status_code=422, detail=f"unknown user_key: {body.user_key!r}")

    picks = await recommend(limit=body.limit, key=user_key, subscribed_quota=_SUBSCRIBED_QUOTA)
    if not picks:
        # Valid profile, but nothing to recommend right now (no tracked candidates
        # / all on cooldown). Empty list, not an error — the key is known.
        return {"recommendations": []}

    # Read the freshly persisted rows back (real ids + created_at) via the feed
    # query — newest ``len(picks)`` rows for this key are exactly what we wrote.
    page = await recs_q.list_recommendations(limit=len(picks), user_key=user_key)
    return {"recommendations": [_rec_json(row) for row in page.rows]}
