"""The Transport contract — notification delivery abstraction (PLAN.md §4.6).

Telegram first, but everything downstream speaks :class:`Notification` /
:class:`Transport` so Discord, email, and webhooks drop in later. Delivery is
mediated by an outbox table so sends are retried and never duplicated.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class Channel(enum.StrEnum):
    TELEGRAM_DM = "telegram_dm"  # streamer's private chat (interactive)
    TELEGRAM_GROUP = "telegram_group"  # broadcast digest (read-only)


@dataclass(slots=True)
class Button:
    """An interactive action attached to a notification (feeds back into ``fit``)."""

    text: str
    #: callback payload, e.g. "feedback:up:<rec_id>" — parsed by the bot.
    action: str


@dataclass(slots=True)
class Notification:
    """A message to deliver, independent of transport.

    ``dedup_key`` lets the outbox guarantee at-most-once delivery of the same
    logical message (e.g. one digest per day per channel).
    """

    channel: Channel
    text: str
    dedup_key: str
    buttons: Sequence[Button] = field(default_factory=list)
    #: transport-specific hints (parse_mode, disable_preview…).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryResult:
    ok: bool
    #: transport message id on success, for later edits/replies.
    message_id: str | None = None
    error: str | None = None
    retryable: bool = False


@runtime_checkable
class Transport(Protocol):
    """Sends notifications over one channel."""

    channel: Channel

    async def send(self, msg: Notification) -> DeliveryResult: ...
