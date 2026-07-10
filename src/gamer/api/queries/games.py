"""Catalog list query (UI_PLAN.md §4 / §5).

UI-M2 turns the catalog into the headline feature: filters (platform, genre,
tracked-only, active-only), an allowlisted sort, and per-row precomputed stats
(current players, 24h delta, a 7-day sparkline, review count) LEFT JOINed from
``game_stats`` — list pages **never** touch ``signals_samples`` (§5.4).

Rules (UI_PLAN.md §4, §9): never ``OFFSET`` — cursors are opaque keyset tokens;
sort/filter values are allowlisted (never interpolated into SQL); all user input
flows through bound params; ILIKE wildcards are escaped so ``%``/``_`` match
literally.

**Keyset for nullable numeric sorts (NULLS LAST).** Every sort has a total
``(sort_value, id)`` order. For the nullable numeric sorts (players / delta /
reviews) the ordering is ``value DESC NULLS LAST, id ASC``. The seek predicate
for "strictly after ``(last_value, last_id)``" is:

* when ``last_value`` is **not** NULL — a row comes after if its value is smaller
  (``col < last_value``), OR it is NULL (NULLs sort last), OR it ties the value
  and has a larger id::

      col < :v  OR  col IS NULL  OR  (col = :v AND id > :last_id)

* when ``last_value`` **is** NULL — we are already in the NULL tail, so only
  NULL rows with a larger id remain::

      col IS NULL AND id > :last_id

The cursor encodes the value with its real type (``float`` or JSON ``null``), so
:func:`gamer.api.deps.decode_cursor` accepts both ``(float, int)`` and
``(None, int)`` for these sorts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.sql.elements import ColumnElement

from gamer.api.deps import decode_cursor, encode_cursor, escape_like
from gamer.db import session_scope
from gamer.db.models import Game, GameStats, Platform

DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
# "active" = has a signal within this window.
_ACTIVE_WINDOW = timedelta(days=7)


class Sort(StrEnum):
    """Allowlisted catalog sort keys (UI_PLAN.md §5 — never interpolate input)."""

    NAME = "name"
    PLAYERS = "players"
    DELTA_24H = "delta_24h"
    REVIEWS = "reviews"
    RELEASE = "release"
    UPDATED = "updated"


# Sorts whose primary key is a nullable numeric column ordered DESC NULLS LAST.
_NULLABLE_NUMERIC = {Sort.PLAYERS, Sort.DELTA_24H, Sort.REVIEWS}


@dataclass(frozen=True, slots=True)
class GameRow:
    """One catalog row: catalog fields + precomputed ``game_stats`` (§5.4)."""

    id: int
    name: str
    platform: str
    genres: list[str]
    tracked: bool
    current_players: float | None = None
    players_24h_delta: float | None = None
    spark: list[float] = field(default_factory=list)
    review_count: float | None = None
    last_signal_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GamePage:
    """A page of catalog rows plus the cursor for the next page (if any)."""

    rows: list[GameRow]
    next_cursor: str | None


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


def _sort_column(sort: Sort) -> Any:
    """The primary sort column for ``sort`` (allowlisted mapping — no input)."""
    mapping: dict[Sort, Any] = {
        Sort.NAME: Game.name,
        Sort.PLAYERS: GameStats.current_players,
        Sort.DELTA_24H: GameStats.players_24h_delta,
        Sort.REVIEWS: GameStats.review_count,
        Sort.RELEASE: Game.release_date,
        Sort.UPDATED: Game.updated_at,
    }
    return mapping[sort]


def _cursor_types(sort: Sort) -> tuple[type, ...]:
    """Expected ``(value, id)`` element types for this sort's cursor."""
    if sort is Sort.NAME:
        return (str, int)
    if sort in _NULLABLE_NUMERIC:
        return (float, int)
    # release_date / updated_at encoded as ISO strings (or null for release).
    return (str, int)


def _apply_order(stmt: Any, sort: Sort) -> Any:
    col = _sort_column(sort)
    if sort is Sort.NAME:
        return stmt.order_by(col.asc(), Game.id.asc())
    if sort in _NULLABLE_NUMERIC or sort is Sort.RELEASE:
        return stmt.order_by(col.desc().nulls_last(), Game.id.asc())
    # updated_at is NOT NULL.
    return stmt.order_by(col.desc(), Game.id.asc())


def _seek_predicate(sort: Sort, last_value: Any, last_id: int) -> ColumnElement[bool]:
    """Keyset "strictly after ``(last_value, last_id)``" for the given sort."""
    col = _sort_column(sort)
    if sort is Sort.NAME:
        return or_(col > last_value, and_(col == last_value, Game.id > last_id))

    if sort in _NULLABLE_NUMERIC:
        # DESC NULLS LAST keyset (see module docstring).
        if last_value is None:
            return and_(col.is_(None), Game.id > last_id)
        return or_(
            col < last_value,
            col.is_(None),
            and_(col == last_value, Game.id > last_id),
        )

    if sort is Sort.RELEASE:
        # release_date DESC NULLS LAST; cursor value is an ISO string or null.
        dt = datetime.fromisoformat(last_value) if isinstance(last_value, str) else None
        if dt is None:
            return and_(col.is_(None), Game.id > last_id)
        return or_(
            col < dt,
            col.is_(None),
            and_(col == dt, Game.id > last_id),
        )

    # updated_at DESC (NOT NULL); cursor value is an ISO string.
    dt = datetime.fromisoformat(last_value) if isinstance(last_value, str) else None
    if dt is None:
        return Game.id > last_id
    return or_(col < dt, and_(col == dt, Game.id > last_id))


def _next_cursor_value(sort: Sort, row_tuple: tuple[Any, ...]) -> Any:
    """Extract the JSON-encodable sort value for the last row from its DB tuple.

    ``row_tuple`` is the raw selected columns (see :func:`_select_columns`), so
    the sort-driving column is read directly rather than reconstructed.
    """
    (
        _id,
        _name,
        _platform,
        _genres,
        _tracked,
        current_players,
        delta,
        _spark,
        review_count,
        _last_signal_at,
        release_date,
        updated_at,
    ) = row_tuple
    if sort is Sort.NAME:
        return _name
    if sort is Sort.PLAYERS:
        return None if current_players is None else float(current_players)
    if sort is Sort.DELTA_24H:
        return None if delta is None else float(delta)
    if sort is Sort.REVIEWS:
        return None if review_count is None else float(review_count)
    if sort is Sort.RELEASE:
        return release_date.isoformat() if release_date is not None else None
    return updated_at.isoformat()


def _select_columns() -> Any:
    return (
        select(
            Game.id,
            Game.name,
            Game.platform,
            Game.genres,
            Game.tracked,
            GameStats.current_players,
            GameStats.players_24h_delta,
            GameStats.players_7d_spark,
            GameStats.review_count,
            GameStats.last_signal_at,
            Game.release_date,
            Game.updated_at,
        )
        .select_from(Game)
        .outerjoin(GameStats, GameStats.game_id == Game.id)
    )


async def list_games(
    search: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    *,
    platform: str | None = None,
    genre: str | None = None,
    tracked_only: bool = False,
    active_only: bool = False,
    sort: str = "name",
    now: datetime | None = None,
) -> GamePage:
    """Keyset-paginated, filterable, sortable catalog listing (UI_PLAN.md §5).

    ``sort`` and ``platform`` are validated against allowlists here (invalid →
    :class:`ValueError`, which the route surfaces as a 422). ``genre`` is matched
    with a JSONB containment test (``genres @> '["X"]'``). List rows come from a
    LEFT JOIN on ``game_stats`` — no ``signals_samples`` access.
    """
    limit = _clamp_limit(limit)
    now = now or datetime.now(UTC)

    try:
        sort_key = Sort(sort)
    except ValueError as exc:
        raise ValueError(f"invalid sort: {sort!r}") from exc

    stmt = _select_columns()

    if search and search.strip():
        pattern = f"%{escape_like(search.strip())}%"
        stmt = stmt.where(func.lower(Game.name).like(func.lower(pattern), escape="\\"))

    if platform is not None:
        try:
            platform_enum = Platform(platform)
        except ValueError as exc:
            raise ValueError(f"invalid platform: {platform!r}") from exc
        stmt = stmt.where(Game.platform == platform_enum.value)

    if genre is not None and genre.strip():
        # JSONB containment via a bound param — never string-interpolated.
        stmt = stmt.where(Game.genres.contains([genre]))

    if tracked_only:
        stmt = stmt.where(Game.tracked.is_(True))

    if active_only:
        stmt = stmt.where(GameStats.last_signal_at >= now - _ACTIVE_WINDOW)

    seek = decode_cursor(cursor, _cursor_types(sort_key))
    if seek is None and cursor:
        # A NULLS-LAST numeric cursor may legitimately carry a null value; retry
        # with a (None-typed) arity check before giving up.
        seek = _decode_null_value_cursor(cursor, sort_key)
    if seek is not None:
        last_value, last_id = seek
        stmt = stmt.where(_seek_predicate(sort_key, last_value, int(last_id)))

    stmt = _apply_order(stmt, sort_key).limit(limit + 1)

    async with session_scope() as session:
        result = (await session.execute(stmt)).all()

    has_more = len(result) > limit
    page = result[:limit]
    rows = [_to_row(rt) for rt in page]

    next_cursor: str | None = None
    if has_more and page:
        last_tuple = tuple(page[-1])
        value = _next_cursor_value(sort_key, last_tuple)
        next_cursor = encode_cursor((value, rows[-1].id))
    return GamePage(rows=rows, next_cursor=next_cursor)


def _decode_null_value_cursor(cursor: str, sort: Sort) -> tuple[Any, ...] | None:
    """Decode a keyset cursor whose sort value is JSON ``null`` (NULLS-LAST tail).

    The strict :func:`decode_cursor` rejects ``null`` for a ``float``/``str``
    slot, so nullable sorts fall back here: accept ``[null, <int id>]``.
    """
    if sort not in _NULLABLE_NUMERIC and sort is not Sort.RELEASE:
        return None
    import base64
    import binascii
    import json

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        parsed = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if (
        isinstance(parsed, list)
        and len(parsed) == 2
        and parsed[0] is None
        and isinstance(parsed[1], int)
        and not isinstance(parsed[1], bool)
    ):
        return (None, parsed[1])
    return None


def _to_row(rt: Any) -> GameRow:
    (
        row_id,
        name,
        platform,
        genres,
        tracked,
        current_players,
        delta,
        spark,
        review_count,
        last_signal_at,
        _release_date,
        _updated_at,
    ) = rt
    return GameRow(
        id=int(row_id),
        name=str(name),
        platform=platform.value if isinstance(platform, Platform) else str(platform),
        genres=list(genres or []),
        tracked=bool(tracked),
        current_players=None if current_players is None else float(current_players),
        players_24h_delta=None if delta is None else float(delta),
        spark=[float(x) for x in (spark or [])],
        review_count=None if review_count is None else float(review_count),
        last_signal_at=last_signal_at,
    )


async def list_genres() -> list[str]:
    """Distinct genres for filter chips (UI_PLAN.md §4.2).

    Sourced from tracked games; if none are tracked yet, falls back to all games.
    A single cache-friendly query using ``jsonb_array_elements_text`` as an
    implicit lateral over the filtered games.
    """
    # A lateral table function expanding each game's genres array to one row per
    # genre; the explicit lateral join keeps ``games`` in the FROM clause.
    genre_tf = func.jsonb_array_elements_text(Game.genres).table_valued("value")

    async def _query(tracked_only: bool) -> list[str]:
        stmt = select(genre_tf.c.value).select_from(Game).join(genre_tf, true())
        if tracked_only:
            stmt = stmt.where(Game.tracked.is_(True))
        stmt = stmt.distinct().order_by(genre_tf.c.value)
        async with session_scope() as session:
            return [str(r[0]) for r in (await session.execute(stmt)).all()]

    genres = await _query(tracked_only=True)
    if not genres:
        genres = await _query(tracked_only=False)
    return genres
