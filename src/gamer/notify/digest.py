"""Compose the daily "top movers" digest into a Notification (M2).

Formatting only — delivery is the transport/outbox's job. Kept transport-agnostic
so the same digest can go to Telegram now and Discord later.
"""

from __future__ import annotations

from datetime import date

from gamer.notify.base import Channel, Notification
from gamer.scoring.base import ScoredRecommendation
from gamer.signals.movers import Mover

_STEAM_STORE = "https://store.steampowered.com/app/"


def _fmt_mover(rank: int, m: Mover) -> str:
    arrow = "📈" if m.delta >= 0 else "📉"
    pct = f" ({m.pct:+.0f}%)" if m.pct is not None else ""
    url = f"{_STEAM_STORE}{m.platform_app_id}"
    return (
        f'{rank}. <a href="{url}">{m.name}</a> {arrow} '
        f"{m.latest:,.0f} players ({m.delta:+,.0f}{pct})"
    )


def build_digest(
    movers: list[Mover],
    *,
    channel: Channel = Channel.TELEGRAM_GROUP,
    for_day: date | None = None,
) -> Notification:
    """Build the read-only digest notification. ``dedup_key`` includes the day so
    exactly one digest per day per channel is ever delivered (outbox enforces it).
    """
    day = for_day or date.today()
    if movers:
        lines = [_fmt_mover(i, m) for i, m in enumerate(movers, start=1)]
        body = "\n".join(lines)
    else:
        body = "No movement to report yet — still gathering player-count data."

    text = f"<b>🎮 Top movers — {day.isoformat()}</b>\n\n{body}"
    return Notification(
        channel=channel,
        text=text,
        dedup_key=f"digest:{channel.value}:{day.isoformat()}",
        meta={"parse_mode": "HTML", "disable_web_page_preview": True},
    )


def _top_reason(rec: ScoredRecommendation) -> str:
    """The single highest-weighted component reason, for the one-line digest."""
    best: tuple[float, str] | None = None
    for key, part in rec.breakdown.items():
        if not isinstance(part, dict) or key.startswith("penalty:"):
            continue
        weighted = part.get("weighted")
        if isinstance(weighted, int | float):
            reason = str(part.get("reason", ""))
            if best is None or weighted > best[0]:
                best = (float(weighted), reason)
    return best[1] if best else ""


def build_scored_digest(
    recs: list[ScoredRecommendation],
    *,
    channel: Channel = Channel.TELEGRAM_GROUP,
    for_day: date | None = None,
    summary: str | None = None,
) -> Notification:
    """Digest built from real recommendations (M3). One line per pick with its
    top "why" reason. Same per-day dedup key as :func:`build_digest`.

    ``summary`` is the optional human-sounding blurb from the LLM (M4). When given,
    it is rendered as an italic intro line above the picks; when ``None`` the digest
    is byte-for-byte what it was before the LLM feature existed.
    """
    day = for_day or date.today()
    if recs:
        lines = [f"{i}. <b>{r.name}</b> — {_top_reason(r)}" for i, r in enumerate(recs, start=1)]
        body = "\n".join(lines)
    else:
        body = "No picks yet — still gathering signal data."

    header = f"<b>🎮 What to stream — {day.isoformat()}</b>"
    if summary:
        text = f"{header}\n\n<i>{summary}</i>\n\n{body}"
    else:
        text = f"{header}\n\n{body}"
    return Notification(
        channel=channel,
        text=text,
        dedup_key=f"digest:{channel.value}:{day.isoformat()}",
        meta={"parse_mode": "HTML", "disable_web_page_preview": True},
    )
