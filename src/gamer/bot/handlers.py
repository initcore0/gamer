"""aiogram v3 command surface (PLAN.md §4.7).

Commands (DM, interactive):
  /help, /start — brief intro to the bot (works in DM and groups)
  /recommend    — top movers right now (M2: naive; M3 swaps in the scorer)
  /why <game>   — explain a recommendation's score breakdown
  /mute <game>  — stop recommending a game
  /track <game>, /untrack <game> — poll / stop polling a game's player count
  /subscribe <genre>, /unsubscribe <genre> — always-cover a genre (M7)
  /prefs        — show current preferences
  /digest on|off — toggle the group digest

Callback buttons (👍/👎/played it) record feedback that later feeds ``fit`` (M3).
This module builds the Router; wiring the Bot/Dispatcher lives in bot/app.py so
this stays unit-testable without a live Telegram connection.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from gamer.catalog.genre_tracking import known_genres, track_subscribed_genres
from gamer.db import session_scope
from gamer.db.models import Feedback, FeedbackVerdict, Game, Recommendation, StreamerPref
from gamer.logging import get_logger
from gamer.scoring.base import ScoredRecommendation
from gamer.scoring.service import recommend as score_recommend
from gamer.signals.movers import Mover, top_movers

log = get_logger("bot")

router = Router(name="gamer")

_PREF_KEY = "default"


def help_text() -> str:
    """Brief intro shown by /help and /start. Pure so it's unit-testable."""
    return (
        "🎮 <b>gamer</b> — I watch the Steam charts and suggest what to stream next.\n"
        "\n"
        "<b>Commands</b>\n"
        "• /recommend — top picks right now, with a one-line reason each\n"
        "• /why &lt;game&gt; — the score breakdown behind a pick\n"
        "• /mute &lt;game&gt; — stop suggesting a game\n"
        "• /track &lt;game&gt; — start polling a game's player count\n"
        "• /untrack &lt;game&gt; — stop polling a game's player count\n"
        "• /subscribe &lt;genre&gt; — always cover a genre (auto-tracks + boosts it)\n"
        "• /unsubscribe &lt;genre&gt; — stop covering a genre\n"
        "• /genres — tap buttons to subscribe/unsubscribe genres\n"
        "• /prefs — show your genres, mutes, and digest setting\n"
        "• /digest on|off — toggle the daily group digest\n"
        "• /help — this message\n"
        "\n"
        "Tap the 👍 / 👎 / ▶️ buttons under a pick to teach me your taste."
    )


@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(help_text(), parse_mode="HTML")


async def _get_prefs() -> StreamerPref:
    async with session_scope() as session:
        row = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == _PREF_KEY))
        ).scalar_one_or_none()
        if row is None:
            row = StreamerPref(key=_PREF_KEY)
            session.add(row)
            await session.flush()
        session.expunge(row)
        return row


def format_movers_reply(movers: list[Mover]) -> str:
    if not movers:
        return "No movers yet — still gathering player-count data. Check back soon."
    lines = ["<b>Top movers right now:</b>"]
    for i, m in enumerate(movers, start=1):
        pct = f" ({m.pct:+.0f}%)" if m.pct is not None else ""
        lines.append(f"{i}. {m.name} — {m.latest:,.0f} players ({m.delta:+,.0f}{pct})")
    return "\n".join(lines)


def _top_reasons(breakdown: dict[str, object], n: int = 2) -> list[str]:
    """The ``n`` highest-weighted component reasons, plus any applied penalty."""
    contribs: list[tuple[float, str]] = []
    penalties: list[str] = []
    for key, part in breakdown.items():
        if not isinstance(part, dict):
            continue
        reason = str(part.get("reason", ""))
        if key.startswith("penalty:"):
            penalties.append(reason)
            continue
        weighted = part.get("weighted")
        if isinstance(weighted, int | float):
            contribs.append((float(weighted), reason))
    contribs.sort(key=lambda x: x[0], reverse=True)
    reasons = [r for _, r in contribs[:n] if r]
    reasons.extend(f"⚠ {r}" for r in penalties)
    return reasons


def format_scored_reply(recs: list[ScoredRecommendation]) -> str:
    lines = ["<b>Recommended for you:</b>"]
    for i, r in enumerate(recs, start=1):
        lines.append(f"{i}. <b>{r.name}</b> — {r.score:.2f}")
        for reason in _top_reasons(r.breakdown):
            lines.append(f"   • {reason}")
    return "\n".join(lines)


@router.message(Command("recommend"))
async def cmd_recommend(message: Message) -> None:
    recs = await score_recommend(limit=5)
    if recs:
        await message.answer(format_scored_reply(recs), parse_mode="HTML")
        return
    # Fallback: the scorer had nothing (no components/candidates yet) → top movers.
    movers = await top_movers(limit=5)
    await message.answer(format_movers_reply(movers), parse_mode="HTML")


@router.message(Command("why"))
async def cmd_why(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: <code>/why &lt;game name&gt;</code>", parse_mode="HTML")
        return
    async with session_scope() as session:
        game = (
            await session.execute(select(Game).where(Game.name.ilike(f"%{query}%")).limit(1))
        ).scalar_one_or_none()
        if game is None:
            await message.answer(f"I don't have a game matching “{query}”.")
            return
        rec = (
            await session.execute(
                select(Recommendation)
                .where(Recommendation.game_id == game.id)
                .order_by(Recommendation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if rec is None or not rec.breakdown:
        await message.answer(
            f"No scored recommendation for <b>{game.name}</b> yet "
            f"(the scoring engine lands in M3).",
            parse_mode="HTML",
        )
        return
    scored = ScoredRecommendation(
        game_id=game.id, name=game.name, score=rec.score, breakdown=rec.breakdown
    )
    await message.answer(f"<b>Why {game.name}</b>\n{scored.why()}", parse_mode="HTML")


@router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: <code>/mute &lt;game name&gt;</code>", parse_mode="HTML")
        return
    async with session_scope() as session:
        game = (
            await session.execute(select(Game).where(Game.name.ilike(f"%{query}%")).limit(1))
        ).scalar_one_or_none()
        if game is None:
            await message.answer(f"No game matching “{query}”.")
            return
        prefs = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == _PREF_KEY))
        ).scalar_one_or_none()
        if prefs is None:
            prefs = StreamerPref(key=_PREF_KEY)
            session.add(prefs)
        muted = set(prefs.muted_game_ids or [])
        muted.add(game.id)
        prefs.muted_game_ids = sorted(muted)
        name = game.name
    await message.answer(f"🔇 Muted <b>{name}</b>. I won't recommend it.", parse_mode="HTML")


async def _set_tracked(query: str, tracked: bool) -> str | None:
    """Set ``tracked`` on the first game matching ``query`` (ilike). Returns the
    game name on success, or ``None`` when nothing matched."""
    async with session_scope() as session:
        game = (
            await session.execute(select(Game).where(Game.name.ilike(f"%{query}%")).limit(1))
        ).scalar_one_or_none()
        if game is None:
            return None
        game.tracked = tracked
        return game.name


@router.message(Command("track"))
async def cmd_track(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: <code>/track &lt;game name&gt;</code>", parse_mode="HTML")
        return
    name = await _set_tracked(query, True)
    if name is None:
        await message.answer(f"No game matching “{query}”.")
        return
    await message.answer(
        f"📈 Tracking <b>{name}</b> — player counts start within the hour.", parse_mode="HTML"
    )


@router.message(Command("untrack"))
async def cmd_untrack(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: <code>/untrack &lt;game name&gt;</code>", parse_mode="HTML")
        return
    name = await _set_tracked(query, False)
    if name is None:
        await message.answer(f"No game matching “{query}”.")
        return
    await message.answer(f"📉 Stopped tracking <b>{name}</b>.", parse_mode="HTML")


def resolve_genre(query: str, catalog: list[str]) -> tuple[str | None, list[str]]:
    """Resolve ``query`` to a catalog genre's canonical casing (case-insensitive).

    Pure. Returns ``(canonical, [])`` on a match; on a miss returns
    ``(None, suggestions)`` with up to 5 close matches (``difflib``).
    """
    lowered = query.strip().lower()
    for genre in catalog:
        if genre.lower() == lowered:
            return genre, []
    suggestions = difflib.get_close_matches(query, catalog, n=5, cutoff=0.5)
    return None, suggestions


def _no_match_reply(query: str, suggestions: list[str]) -> str:
    if suggestions:
        hint = ", ".join(suggestions)
        return f"No genre matching “{query}”. Did you mean: <b>{hint}</b>?"
    return f"No genre matching “{query}”."


async def _toggle_subscription(genre_canonical: str) -> tuple[bool, int]:
    """Toggle a canonical genre in the streamer's ``subscribed_genres``.

    Flips membership case-insensitively, storing the canonical casing. On a
    *subscribe* it immediately runs :func:`track_subscribed_genres` for that genre
    (coverage starts now, not at the next hourly tick); on an *unsubscribe* nothing
    is un-tracked (consistent with the never-untrack design).

    Returns ``(subscribed_now, tracked_count)`` where ``tracked_count`` is the
    number of games newly tracked (always ``0`` on an unsubscribe). Shared by
    ``/subscribe``, ``/unsubscribe`` and the ``/genres`` button callback.
    """
    lowered = genre_canonical.lower()
    async with session_scope() as session:
        prefs = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == _PREF_KEY))
        ).scalar_one_or_none()
        if prefs is None:
            prefs = StreamerPref(key=_PREF_KEY)
            session.add(prefs)
        subs = list(prefs.subscribed_genres or [])
        already = any(s.lower() == lowered for s in subs)
        if already:
            prefs.subscribed_genres = [s for s in subs if s.lower() != lowered]
            subscribed_now = False
        else:
            subs.append(genre_canonical)
            prefs.subscribed_genres = subs
            subscribed_now = True

    tracked = await track_subscribed_genres([genre_canonical]) if subscribed_now else 0
    return subscribed_now, tracked


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(
            "Usage: <code>/subscribe &lt;genre&gt;</code> — or use /genres for buttons.",
            parse_mode="HTML",
        )
        return
    canonical, suggestions = resolve_genre(query, await known_genres())
    if canonical is None:
        await message.answer(_no_match_reply(query, suggestions), parse_mode="HTML")
        return

    # /subscribe is idempotently additive: ensure membership, then track. Only
    # toggle when not already subscribed so a repeat /subscribe doesn't remove it.
    async with session_scope() as session:
        prefs = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == _PREF_KEY))
        ).scalar_one_or_none()
        subs = list(prefs.subscribed_genres or []) if prefs else []
        already = any(s.lower() == canonical.lower() for s in subs)
    if already:
        tracked = await track_subscribed_genres([canonical])
    else:
        _, tracked = await _toggle_subscription(canonical)
    await message.answer(
        f"🧩 Subscribed to <b>{canonical}</b> — tracking {tracked} games in this genre.",
        parse_mode="HTML",
    )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, command: CommandObject) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer("Usage: <code>/unsubscribe &lt;genre&gt;</code>", parse_mode="HTML")
        return
    lowered = query.lower()
    async with session_scope() as session:
        prefs = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == _PREF_KEY))
        ).scalar_one_or_none()
        subs = list(prefs.subscribed_genres or []) if prefs else []
        match = next((s for s in subs if s.lower() == lowered), None)
        if match is None or prefs is None:
            await message.answer(f"You're not subscribed to “{query}”.")
            return
        prefs.subscribed_genres = [s for s in subs if s.lower() != lowered]
    await message.answer(
        f"🚫 Unsubscribed from <b>{match}</b>. (Already-tracked games stay tracked.)",
        parse_mode="HTML",
    )


@router.message(Command("prefs"))
async def cmd_prefs(message: Message) -> None:
    prefs = await _get_prefs()
    liked = ", ".join(prefs.liked_genres) or "—"
    blocked = ", ".join(prefs.blocked_genres) or "—"
    subscribed = ", ".join(prefs.subscribed_genres) or "—"
    await message.answer(
        "<b>Your preferences</b>\n"
        f"Liked genres: {liked}\n"
        f"Blocked genres: {blocked}\n"
        f"Subscribed genres: {subscribed}\n"
        f"Muted games: {len(prefs.muted_game_ids or [])}\n"
        f"Group digest: {'on' if prefs.digest_enabled else 'off'}",
        parse_mode="HTML",
    )


@router.message(Command("digest"))
async def cmd_digest(message: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip().lower()
    if arg not in {"on", "off"}:
        await message.answer("Usage: <code>/digest on|off</code>", parse_mode="HTML")
        return
    enabled = arg == "on"
    async with session_scope() as session:
        prefs = (
            await session.execute(select(StreamerPref).where(StreamerPref.key == _PREF_KEY))
        ).scalar_one_or_none()
        if prefs is None:
            prefs = StreamerPref(key=_PREF_KEY)
            session.add(prefs)
        prefs.digest_enabled = enabled
    await message.answer(f"Group digest turned <b>{arg}</b>.", parse_mode="HTML")


def parse_feedback_action(data: str) -> tuple[FeedbackVerdict, int] | None:
    """Parse a callback payload ``feedback:<verdict>:<rec_id>`` → (verdict, rec_id).

    Returns None for anything that isn't a well-formed feedback callback.
    """
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "feedback":
        return None
    try:
        verdict = FeedbackVerdict(parts[1])
        rec_id = int(parts[2])
    except (ValueError, KeyError):
        return None
    return verdict, rec_id


@router.callback_query(F.data.startswith("feedback:"))
async def on_feedback(callback: CallbackQuery) -> None:
    parsed = parse_feedback_action(callback.data or "")
    if parsed is None:
        await callback.answer("Unrecognized action.")
        return
    verdict, rec_id = parsed
    async with session_scope() as session:
        rec_exists = (
            await session.execute(select(Recommendation.id).where(Recommendation.id == rec_id))
        ).scalar_one_or_none()
        if rec_exists is None:
            # Stale or forged callback — answer gracefully instead of hitting the
            # feedback.rec_id foreign key.
            await callback.answer("That recommendation is gone.")
            return
        session.add(Feedback(rec_id=rec_id, verdict=verdict))
    log.info("feedback_recorded", rec_id=rec_id, verdict=verdict.value)
    await callback.answer({"up": "👍 Noted!", "down": "👎 Got it.", "played": "🎮 Nice!"}[verdict])


# ── /genres — tappable genre-subscription panel (M7 UX) ───────────────────────

#: Max genre buttons shown per page (nav row is added on top when more exist).
GENRES_PER_PAGE = 16
#: Genre buttons per keyboard row.
GENRES_PER_ROW = 2


def genre_digest(genre: str) -> str:
    """First 12 hex chars of sha256(genre.lower()) — a stable short id for a genre.

    Used in callback_data instead of the raw (possibly long/unicode) genre name so
    the payload stays well under Telegram's 64-byte limit.
    """
    return hashlib.sha256(genre.lower().encode("utf-8")).hexdigest()[:12]


def paginate(
    genres: list[str], page: int, per_page: int = GENRES_PER_PAGE
) -> tuple[list[str], int]:
    """Return ``(items_on_page, total_pages)`` for ``genres`` at 0-based ``page``.

    Pure. ``page`` is clamped to ``[0, total_pages - 1]``. An empty list yields
    ``([], 1)`` (one empty page) so callers never divide by zero.
    """
    total_pages = max(1, (len(genres) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    return genres[start : start + per_page], total_pages


def build_genres_keyboard(
    genres: list[str], subscribed: set[str], page: int
) -> InlineKeyboardMarkup | None:
    """Build the genre-subscription keyboard for one page.

    ``genres`` are sorted case-insensitively; subscribed ones render as
    ``"✅ Puzzle"``. Buttons are laid out :data:`GENRES_PER_ROW` per row, with a
    nav row (``◀️`` / ``page x/y`` / ``▶️``) appended when there is more than one
    page. Returns ``None`` for an empty catalog (no keyboard at all).

    ``subscribed`` is a set of subscribed genre names, matched case-insensitively.
    """
    if not genres:
        return None
    ordered = sorted(genres, key=str.lower)
    sub_lower = {s.lower() for s in subscribed}
    items, total_pages = paginate(ordered, page)
    # paginate clamps; recover the effective page for the nav label/data.
    eff_page = max(0, min(page, total_pages - 1))

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for genre in items:
        mark = "✅ " if genre.lower() in sub_lower else ""
        row.append(
            InlineKeyboardButton(
                text=f"{mark}{genre}",
                callback_data=f"genre:t:{genre_digest(genre)}:{eff_page}",
            )
        )
        if len(row) == GENRES_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if eff_page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"genre:p:{eff_page - 1}"))
        nav.append(
            InlineKeyboardButton(
                text=f"page {eff_page + 1}/{total_pages}", callback_data="genre:noop"
            )
        )
        if eff_page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"genre:p:{eff_page + 1}"))
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


@dataclass(frozen=True)
class GenreAction:
    """A parsed ``genre:`` callback. ``kind`` is ``toggle``, ``page`` or ``noop``.

    ``digest`` is set only for ``toggle``; ``page`` is set for ``toggle`` and
    ``page`` (0 for ``noop``).
    """

    kind: str
    digest: str | None
    page: int


def parse_genre_action(data: str) -> GenreAction | None:
    """Parse a ``genre:`` callback payload → :class:`GenreAction`, else ``None``.

    Grammar:
      * ``genre:t:<digest>:<page>`` — toggle a genre (digest = 12 hex chars)
      * ``genre:p:<page>``          — navigate to a page
      * ``genre:noop``              — page indicator, does nothing

    Returns ``None`` for anything malformed, tampered, or out of range (negative
    page, non-hex/wrong-length digest, garbage) — the caller answers gracefully.
    """
    parts = data.split(":")
    if not parts or parts[0] != "genre":
        return None
    kind = parts[1] if len(parts) > 1 else ""
    if kind == "noop":
        return GenreAction("noop", None, 0) if len(parts) == 2 else None
    if kind == "p":
        if len(parts) != 3:
            return None
        try:
            page = int(parts[2])
        except ValueError:
            return None
        return GenreAction("page", None, page) if page >= 0 else None
    if kind == "t":
        if len(parts) != 4:
            return None
        digest = parts[2]
        if len(digest) != 12 or not all(c in "0123456789abcdef" for c in digest):
            return None
        try:
            page = int(parts[3])
        except ValueError:
            return None
        return GenreAction("toggle", digest, page) if page >= 0 else None
    return None


async def _subscribed_set() -> set[str]:
    """The streamer's current subscribed genres as a set (empty when no prefs)."""
    prefs = await _get_prefs()
    return set(prefs.subscribed_genres or [])


@router.message(Command("genres"))
async def cmd_genres(message: Message) -> None:
    genres = await known_genres()
    keyboard = build_genres_keyboard(genres, await _subscribed_set(), 0)
    if keyboard is None:
        await message.answer("No genres in the catalog yet — check back once games are indexed.")
        return
    await message.answer("🧩 Tap a genre to subscribe/unsubscribe:", reply_markup=keyboard)


async def _rerender_genres(callback: CallbackQuery, page: int) -> None:
    """Rebuild the genre keyboard for ``page`` and edit the message markup in place.

    Swallows the aiogram "message is not modified" edge (double-tap / same markup).
    """
    genres = await known_genres()
    keyboard = build_genres_keyboard(genres, await _subscribed_set(), page)
    message = callback.message
    # message may be None or an InaccessibleMessage (too old to edit) — both lack a
    # usable edit target. Guard on the method rather than the concrete type so tests
    # can pass a lightweight stand-in.
    edit = getattr(message, "edit_reply_markup", None)
    if edit is None or keyboard is None:
        return
    try:
        await edit(reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if "message is not modified" not in (exc.message or ""):
            raise


@router.callback_query(F.data.startswith("genre:"))
async def on_genre(callback: CallbackQuery) -> None:
    action = parse_genre_action(callback.data or "")
    if action is None:
        await callback.answer("Unrecognized action.")
        return

    try:
        if action.kind == "noop":
            await callback.answer()
            return

        if action.kind == "page":
            await _rerender_genres(callback, action.page)
            await callback.answer()
            return

        # Toggle: resolve digest → canonical genre over the live catalog.
        genres = await known_genres()
        canonical = next((g for g in genres if genre_digest(g) == action.digest), None)
        if canonical is None:
            await callback.answer("That genre is gone.")
            await _rerender_genres(callback, action.page)
            return

        subscribed_now, tracked = await _toggle_subscription(canonical)
        if subscribed_now:
            await callback.answer(f"🧩 Subscribed to {canonical} — tracking {tracked} games.")
        else:
            await callback.answer(f"Unsubscribed from {canonical}.")
        await _rerender_genres(callback, action.page)
    except TelegramBadRequest:
        raise
    except Exception as exc:  # degrade, don't crash: DB error etc.
        log.warning("genre_callback_failed", error=str(exc))
        await callback.answer("Something went wrong — try again.")
