"""Compose the daily "top movers" digest into a Notification (M2).

Formatting only — delivery is the transport/outbox's job. Kept transport-agnostic
so the same digest can go to Telegram now and Discord later.
"""

from __future__ import annotations

from datetime import date
from html import escape

from gamer.notify.base import Channel, Notification
from gamer.scoring.base import ScoredRecommendation
from gamer.signals.movers import Mover

_STEAM_STORE = "https://store.steampowered.com/app/"


def _is_subscribed(rec: ScoredRecommendation, subscribed: set[str]) -> bool:
    """True when any of the rec's genres matches a subscribed genre (case-insensitive)."""
    return any(g.lower() in subscribed for g in rec.genres)


def apply_genre_quota(
    ranked: list[ScoredRecommendation],
    subscribed: list[str],
    limit: int,
    slots: int = 3,
) -> list[ScoredRecommendation]:
    """Reserve digest slots for subscribed-genre picks (GENRE_SUBS_PLAN.md, M7).

    Pure. Given the *full* ranked list (best-first), returns the final display list
    of length ``min(limit, len(ranked))`` in which at least
    ``min(slots, available_subscribed)`` entries are subscribed-genre games — where
    ``available_subscribed`` is how many subscribed-genre picks exist anywhere in
    ``ranked``.

    When the natural top-``limit`` cut already meets the quota, it is returned
    unchanged. Otherwise the highest-scoring subscribed-genre picks from *below* the
    cut are promoted, each replacing the lowest-scoring non-subscribed pick in the
    cut. The result is re-sorted by score so relative score order is preserved.

    Byte-identical to a plain ``ranked[:limit]`` when ``subscribed`` is empty, when
    no candidate matches, when the cut already satisfies the quota, or when
    ``slots <= 0``.
    """
    cut = ranked[:limit]
    if not subscribed or slots <= 0 or not cut:
        return cut

    subs = {g.lower() for g in subscribed}
    in_cut_subscribed = [r for r in cut if _is_subscribed(r, subs)]

    # How many subscribed picks exist across the entire ranked pool caps the target.
    total_subscribed = sum(1 for r in ranked if _is_subscribed(r, subs))
    target = min(slots, total_subscribed, limit)
    if len(in_cut_subscribed) >= target:
        return cut

    need = target - len(in_cut_subscribed)
    # Subscribed picks below the cut, best-first (ranked is already best-first).
    below_subscribed = [r for r in ranked[limit:] if _is_subscribed(r, subs)]
    promote = below_subscribed[:need]
    if not promote:
        return cut

    # Drop the lowest-scoring non-subscribed picks in the cut to make room, keeping
    # every subscribed pick already in the cut.
    non_subscribed_in_cut = [r for r in cut if not _is_subscribed(r, subs)]
    # Lowest score last in `cut` order; drop from the tail (lowest score first).
    drop = set(id(r) for r in sorted(non_subscribed_in_cut, key=lambda r: r.score)[: len(promote)])
    kept = [r for r in cut if id(r) not in drop]

    result = kept + promote
    result.sort(key=lambda r: r.score, reverse=True)
    return result


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


def _game_link(base_url: str, rec: ScoredRecommendation) -> str:
    """A per-game deep link ``<a href="{base}/games/{id}">↗</a>`` (UI_PLAN.md §6).

    The base URL is operator-configured, not user input, but it is still HTML-
    escaped so a stray ``&``/``<`` can never break Telegram's HTML parse_mode.
    Discord's ``html_to_discord_markdown`` already converts ``<a href>`` to a
    markdown link, so the same markup works on both channels.
    """
    href = escape(f"{base_url}/games/{rec.game_id}", quote=True)
    return f' <a href="{href}">↗</a>'


def build_scored_digest(
    recs: list[ScoredRecommendation],
    *,
    channel: Channel = Channel.TELEGRAM_GROUP,
    for_day: date | None = None,
    summary: str | None = None,
    public_base_url: str = "",
) -> Notification:
    """Digest built from real recommendations (M3). One line per pick with its
    top "why" reason. Same per-day dedup key as :func:`build_digest`.

    ``summary`` is the optional human-sounding blurb from the LLM (M4). When given,
    it is rendered as an italic intro line above the picks; when ``None`` the digest
    is byte-for-byte what it was before the LLM feature existed.

    ``public_base_url`` (UI_PLAN.md §6) is the web UI's public origin. When set,
    each pick gains a ``{base}/games/{id}`` deep link so a bot message lands on
    the game page. Empty (the default) => no link appended, and the digest is
    byte-identical to before this feature.
    """
    day = for_day or date.today()
    base = public_base_url.rstrip("/")
    if recs:
        lines = [
            f"{i}. <b>{r.name}</b> — {_top_reason(r)}{_game_link(base, r) if base else ''}"
            for i, r in enumerate(recs, start=1)
        ]
        body = "\n".join(lines)
    else:
        body = "No picks yet — still gathering signal data."

    header = f"<b>🎮 What to stream — {day.isoformat()}</b>"
    if summary:
        # LLM output is untrusted markup: a stray < or & would make Telegram's
        # HTML parse_mode reject the whole message (permanent send failure).
        text = f"{header}\n\n<i>{escape(summary)}</i>\n\n{body}"
    else:
        text = f"{header}\n\n{body}"
    return Notification(
        channel=channel,
        text=text,
        dedup_key=f"digest:{channel.value}:{day.isoformat()}",
        meta={"parse_mode": "HTML", "disable_web_page_preview": True},
    )
