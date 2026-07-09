"""aiogram v3 command surface (PLAN.md §4.7).

Commands (DM, interactive):
  /recommend    — top movers right now (M2: naive; M3 swaps in the scorer)
  /why <game>   — explain a recommendation's score breakdown
  /mute <game>  — stop recommending a game
  /prefs        — show current preferences
  /digest on|off — toggle the group digest

Callback buttons (👍/👎/played it) record feedback that later feeds ``fit`` (M3).
This module builds the Router; wiring the Bot/Dispatcher lives in bot/app.py so
this stays unit-testable without a live Telegram connection.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import Feedback, FeedbackVerdict, Game, Recommendation, StreamerPref
from gamer.logging import get_logger
from gamer.scoring.base import ScoredRecommendation
from gamer.scoring.service import recommend as score_recommend
from gamer.signals.movers import Mover, top_movers

log = get_logger("bot")

router = Router(name="gamer")

_PREF_KEY = "default"


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


@router.message(Command("prefs"))
async def cmd_prefs(message: Message) -> None:
    prefs = await _get_prefs()
    liked = ", ".join(prefs.liked_genres) or "—"
    blocked = ", ".join(prefs.blocked_genres) or "—"
    await message.answer(
        "<b>Your preferences</b>\n"
        f"Liked genres: {liked}\n"
        f"Blocked genres: {blocked}\n"
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
        session.add(Feedback(rec_id=rec_id, verdict=verdict))
    log.info("feedback_recorded", rec_id=rec_id, verdict=verdict.value)
    await callback.answer({"up": "👍 Noted!", "down": "👎 Got it.", "played": "🎮 Nice!"}[verdict])
