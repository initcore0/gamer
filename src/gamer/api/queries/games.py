"""Catalog list query (UI_PLAN.md §4 / §5).

UI-M1 ships a plain, keyset-paginated listing of ``games`` ordered by
``(name, id)``. Search is a case-insensitive ``ILIKE '%q%'`` on the name —
made fast by the trigram GIN index on ``lower(name)`` added in the UI-M1
migration. Filters, sorts, and per-row stats/sparklines arrive in UI-M2; the
signature is kept narrow so the M2 agent can extend it without breaking callers.

Rules (UI_PLAN.md §4): never ``OFFSET`` — cursors are opaque ``(name, id)``
tokens; user input is never interpolated into SQL text (bound params only) and
ILIKE wildcards are escaped so ``%``/``_`` match literally.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, func, or_, select

from gamer.api.deps import decode_cursor, encode_cursor, escape_like
from gamer.db import session_scope
from gamer.db.models import Game, Platform

DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class GameRow:
    """One catalog row (the subset UI-M1 renders)."""

    id: int
    name: str
    platform: str
    genres: list[str]
    tracked: bool


@dataclass(frozen=True, slots=True)
class GamePage:
    """A page of catalog rows plus the cursor for the next page (if any)."""

    rows: list[GameRow]
    next_cursor: str | None


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


async def list_games(
    search: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> GamePage:
    """Keyset-paginated catalog listing ordered by ``(name, id)``.

    ``search`` (when non-blank) filters names with a literal, wildcard-escaped
    ``ILIKE '%search%'``. ``cursor`` is the opaque token from a previous page's
    :attr:`GamePage.next_cursor`; a missing/garbage token starts at page 1.
    """
    limit = _clamp_limit(limit)
    stmt = select(Game.id, Game.name, Game.platform, Game.genres, Game.tracked)

    if search and search.strip():
        pattern = f"%{escape_like(search.strip())}%"
        # Bound parameter + explicit ESCAPE; the trgm index is on lower(name).
        stmt = stmt.where(func.lower(Game.name).like(func.lower(pattern), escape="\\"))

    # Keyset: rows strictly after the last (name, id) seen. Ordering is total
    # because (name, id) is unique (id is the PK), so no row is skipped/repeated.
    seek = decode_cursor(cursor, (str, int))
    if seek is not None:
        last_name, last_id = seek
        stmt = stmt.where(
            or_(
                Game.name > last_name,
                and_(Game.name == last_name, Game.id > last_id),
            )
        )

    stmt = stmt.order_by(Game.name.asc(), Game.id.asc()).limit(limit + 1)

    async with session_scope() as session:
        result = (await session.execute(stmt)).all()

    has_more = len(result) > limit
    page = result[:limit]
    rows = [
        GameRow(
            id=row_id,
            name=name,
            platform=platform.value if isinstance(platform, Platform) else str(platform),
            genres=list(genres or []),
            tracked=bool(tracked),
        )
        for row_id, name, platform, genres, tracked in page
    ]
    next_cursor = encode_cursor((rows[-1].name, rows[-1].id)) if has_more and rows else None
    return GamePage(rows=rows, next_cursor=next_cursor)
