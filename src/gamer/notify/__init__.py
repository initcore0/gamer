"""Notification transports (Telegram first; Discord/webhook later)."""

from __future__ import annotations

from gamer.config import Settings, get_settings
from gamer.notify.base import (
    Button,
    Channel,
    DeliveryResult,
    Notification,
    Transport,
)
from gamer.notify.discord import (
    DiscordWebhook,
    build_discord_transport,
    html_to_discord_markdown,
)
from gamer.notify.outbox import (
    DispatchStats,
    NextState,
    decide_next_state,
    deserialize_notification,
    dispatch_pending,
    enqueue,
    serialize_notification,
)
from gamer.notify.telegram import (
    TelegramDM,
    TelegramGroup,
    build_bot,
    build_inline_keyboard,
    build_telegram_transports,
)


def build_all_transports(settings: Settings | None = None) -> dict[Channel, Transport]:
    """All configured transports keyed by channel, for the outbox dispatcher.

    Merges the Telegram transports with the Discord webhook when it's enabled.
    Reuses :func:`build_telegram_transports` and :func:`build_discord_transport`
    verbatim so this stays a thin composition seam — new transports slot in here.
    """
    settings = settings or get_settings()
    transports: dict[Channel, Transport] = dict(build_telegram_transports(settings))
    discord = build_discord_transport(settings)
    if discord is not None:
        transports[Channel.DISCORD] = discord
    return transports


async def aclose_transports(transports: dict[Channel, Transport]) -> None:
    """Close each distinct transport instance's underlying HTTP resources once.

    Both Telegram transports share one aiogram Bot (same instance would be
    closed twice without the id-dedup); Discord owns its own client. Transports
    without an ``aclose`` are skipped — the protocol doesn't require one.
    """
    seen: set[int] = set()
    for transport in transports.values():
        if id(transport) in seen:
            continue
        seen.add(id(transport))
        aclose = getattr(transport, "aclose", None)
        if aclose is not None:
            await aclose()


__all__ = [
    "Button",
    "Channel",
    "DeliveryResult",
    "DiscordWebhook",
    "DispatchStats",
    "NextState",
    "Notification",
    "TelegramDM",
    "TelegramGroup",
    "Transport",
    "aclose_transports",
    "build_all_transports",
    "build_bot",
    "build_discord_transport",
    "build_inline_keyboard",
    "build_telegram_transports",
    "decide_next_state",
    "deserialize_notification",
    "dispatch_pending",
    "enqueue",
    "html_to_discord_markdown",
    "serialize_notification",
]
