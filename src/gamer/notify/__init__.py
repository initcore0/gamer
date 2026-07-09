"""Notification transports (Telegram first; Discord/webhook later)."""

from __future__ import annotations

from gamer.notify.base import (
    Button,
    Channel,
    DeliveryResult,
    Notification,
    Transport,
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

__all__ = [
    "Button",
    "Channel",
    "DeliveryResult",
    "DispatchStats",
    "NextState",
    "Notification",
    "TelegramDM",
    "TelegramGroup",
    "Transport",
    "build_bot",
    "build_inline_keyboard",
    "build_telegram_transports",
    "decide_next_state",
    "deserialize_notification",
    "dispatch_pending",
    "enqueue",
    "serialize_notification",
]
