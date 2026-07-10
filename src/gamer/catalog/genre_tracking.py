"""Genre subscriptions — auto-tracking coverage job (GENRE_SUBS_PLAN.md, M7).

For each *subscribed* genre, mark ``tracked=True`` on the top ``N`` games of that
genre so the player-count poller starts sampling them and they enter the scoring
candidate pool. Niche genres never reach Steam's top charts, so without this the
auto-tracker never samples them.

Design notes:

* **Bounded.** At most :data:`TRACK_TOP_N` games per genre are ever touched, so a
  genre full of shovelware can't balloon the tracked set.
* **Ranking.** Top games by ``game_stats.review_count`` (a LEFT JOIN so games
  without stats still qualify), with a deterministic tiebreak/fallback of newest
  release then ``id`` — so the selection is stable run to run.
* **Case-insensitive match.** The genre comparison lowercases both sides via a
  ``jsonb_array_elements_text`` EXISTS with a *bound* parameter (never string
  interpolation, and never ``genres.contains`` which is case-sensitive).
* **Never un-tracks.** The job only ever *sets* ``tracked=True`` — charts,
  ``/track``, and prior subscriptions stay honored.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Select,
    and_,
    exists,
    func,
    literal,
    select,
    true,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gamer.db import session_scope
from gamer.db.models import Game, GameStats, StreamerPref
from gamer.logging import get_logger

log = get_logger("catalog.genre_tracking")

#: Cap on games auto-tracked per subscribed genre (module constant — GENRE_SUBS_PLAN.md).
TRACK_TOP_N = 200

_PREF_KEY = "default"


def _genre_match(genre: str) -> ColumnElement[bool]:
    """A case-insensitive EXISTS over ``games.genres`` for one genre.

    Expands the JSONB array with ``jsonb_array_elements_text`` and compares each
    element to the *bound* ``genre`` parameter, both lowercased. Bound param only —
    no f-string SQL.
    """
    elem = func.jsonb_array_elements_text(Game.genres).table_valued("value")
    return exists(
        select(literal(1)).select_from(elem).where(func.lower(elem.c.value) == func.lower(genre))
    )


def _top_game_ids_stmt(genre: str) -> Select[tuple[int]]:
    """Statement selecting the top ``TRACK_TOP_N`` game ids for ``genre``.

    Ranked by ``game_stats.review_count`` DESC NULLS LAST (LEFT JOIN so statless
    games still qualify), tiebreak newest release then id — deterministic.
    """
    return (
        select(Game.id)
        .outerjoin(GameStats, GameStats.game_id == Game.id)
        .where(_genre_match(genre))
        .order_by(
            GameStats.review_count.desc().nulls_last(),
            Game.release_date.desc().nulls_last(),
            Game.id,
        )
        .limit(TRACK_TOP_N)
    )


async def _subscribed_genres(session: AsyncSession, key: str = _PREF_KEY) -> list[str]:
    """The streamer's subscribed genres (empty when no prefs row exists)."""
    subs = (
        await session.execute(select(StreamerPref.subscribed_genres).where(StreamerPref.key == key))
    ).scalar_one_or_none()
    return list(subs or [])


async def track_subscribed_genres(genres: list[str] | None = None) -> int:
    """Mark ``tracked=True`` on the top games of each subscribed genre.

    ``genres`` overrides the stored subscription list (used by ``/subscribe`` to
    kick off coverage for a single genre immediately). When ``None`` the prefs'
    ``subscribed_genres`` are used.

    Only ever sets ``tracked=True`` (never un-tracks). Returns the number of games
    *newly* tracked (already-tracked games in the selection are not counted). The
    per-genre selection is bounded by :data:`TRACK_TOP_N`.

    Degrades gracefully: a failure on one genre is logged and skipped rather than
    aborting the whole pass.
    """
    async with session_scope() as session:
        target = genres if genres is not None else await _subscribed_genres(session)
        if not target:
            log.info("genre_track_noop", reason="no subscribed genres")
            return 0

        newly_tracked = 0
        for genre in target:
            try:
                ids = [r[0] for r in (await session.execute(_top_game_ids_stmt(genre))).all()]
                if not ids:
                    continue
                # Count + flip only the not-yet-tracked subset, so the return value
                # is "newly tracked" and we never write over already-tracked rows.
                result = await session.execute(
                    update(Game)
                    .where(and_(Game.id.in_(ids), Game.tracked.is_(False)))
                    .values(tracked=True)
                )
                newly_tracked += cast("CursorResult[Any]", result).rowcount or 0
            except Exception as exc:  # one bad genre must not abort the pass
                log.warning("genre_track_genre_failed", genre=genre, error=str(exc))

        log.info(
            "genre_track",
            genres=list(target),
            newly_tracked=newly_tracked,
        )
        return newly_tracked


async def known_genres() -> list[str]:
    """Distinct genres present in the catalog, sorted — for /subscribe validation.

    Shared here (rather than importing ``api.queries.games.list_genres`` into the
    bot) so the bot has no dependency on the API layer. Unlike ``list_genres`` this
    always considers *all* games (not tracked-first), since a subscription's whole
    point is to reach games that aren't tracked yet.
    """
    elem = func.jsonb_array_elements_text(Game.genres).table_valued("value")
    stmt = (
        select(elem.c.value).select_from(Game).join(elem, true()).distinct().order_by(elem.c.value)
    )
    async with session_scope() as session:
        return [str(r[0]) for r in (await session.execute(stmt)).all()]
