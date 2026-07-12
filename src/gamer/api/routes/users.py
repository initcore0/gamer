"""Preference-profile routes (API_CONTRACT.md — user switcher).

``GET /api/v1/users`` lists the preference profiles the React user-switcher
offers. JSON-only (there is no HTML twin for the switcher). All SQL lives in
``queries.users``; this module only shapes the JSON.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from gamer.api.queries import users as users_q

router = APIRouter()


@router.get(
    "/api/v1/users",
    tags=["users"],
    summary="List preference profiles",
    response_model=None,
)
async def users_json() -> dict[str, Any]:
    """Every preference profile for the user switcher (oldest first)."""
    rows = await users_q.list_users()
    return {
        "users": [
            {
                "key": row.key,
                "label": row.label,
                "liked_genres": row.liked_genres,
                "blocked_genres": row.blocked_genres,
                "subscribed_genres": row.subscribed_genres,
                "muted_count": row.muted_count,
                "digest_enabled": row.digest_enabled,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }
