"""Outbox dispatcher — reliable, deduplicated delivery (PLAN.md §4.6).

The outbox decouples *deciding to notify* from *actually sending*:

* :func:`enqueue` inserts a PENDING row, upserting on the
  ``(channel, dedup_key)`` unique constraint so the same logical message is
  never queued twice.
* :func:`dispatch_pending` drains PENDING rows oldest-first, calls the matching
  :class:`~gamer.notify.base.Transport`, and records the outcome.

The retry / dedup *decisions* live in pure, DB-free functions
(:func:`serialize_notification`, :func:`deserialize_notification`,
:func:`decide_next_state`) so they can be unit-tested without a database.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gamer.db import session_scope
from gamer.db.models import Outbox, OutboxStatus
from gamer.logging import get_logger
from gamer.notify.base import (
    Button,
    Channel,
    DeliveryResult,
    Notification,
    Transport,
)

_log = get_logger(__name__)

DEFAULT_MAX_BATCH = 50
DEFAULT_MAX_ATTEMPTS = 5


# ── Pure logic (no DB) ────────────────────────────────────────────────────────


def serialize_notification(msg: Notification) -> dict[str, Any]:
    """Serialize a :class:`Notification` into a JSONB-safe payload dict.

    The ``channel`` and ``dedup_key`` live in dedicated columns; the payload
    carries everything needed to rebuild the message at dispatch time.
    """
    return {
        "text": msg.text,
        "buttons": [{"text": b.text, "action": b.action} for b in msg.buttons],
        "meta": msg.meta,
    }


def deserialize_notification(
    channel: Channel, dedup_key: str, payload: Mapping[str, Any]
) -> Notification:
    """Rebuild a :class:`Notification` from a stored outbox row."""
    buttons = [Button(text=b["text"], action=b["action"]) for b in payload.get("buttons", [])]
    return Notification(
        channel=channel,
        text=payload["text"],
        dedup_key=dedup_key,
        buttons=buttons,
        meta=dict(payload.get("meta", {})),
    )


@dataclass(frozen=True, slots=True)
class NextState:
    """The outcome of applying a :class:`DeliveryResult` to an outbox row."""

    status: OutboxStatus
    attempts: int
    last_error: str | None
    message_id: str | None
    delivered: bool


def decide_next_state(
    *,
    current_attempts: int,
    result: DeliveryResult,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> NextState:
    """Decide a row's next state from a delivery attempt — pure, DB-free.

    * Success → SENT, record ``message_id``.
    * Retryable failure → stay PENDING and bump ``attempts``; flip to FAILED
      once ``attempts`` reaches ``max_attempts`` (retries exhausted).
    * Permanent failure → FAILED immediately (attempts still bumped, for audit).
    """
    attempts = current_attempts + 1

    if result.ok:
        return NextState(
            status=OutboxStatus.SENT,
            attempts=attempts,
            last_error=None,
            message_id=result.message_id,
            delivered=True,
        )

    if result.retryable and attempts < max_attempts:
        status = OutboxStatus.PENDING
    else:
        status = OutboxStatus.FAILED

    return NextState(
        status=status,
        attempts=attempts,
        last_error=result.error,
        message_id=None,
        delivered=False,
    )


# ── Stats ─────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class DispatchStats:
    """Counts from a :func:`dispatch_pending` run."""

    processed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    skipped: int = 0  # PENDING rows with no matching transport


# ── DB-backed operations ──────────────────────────────────────────────────────


async def enqueue(msg: Notification, *, session: AsyncSession | None = None) -> None:
    """Insert a PENDING outbox row, ignoring duplicates.

    Dedup is enforced by the ``(channel, dedup_key)`` unique constraint via
    ``ON CONFLICT DO NOTHING`` — a second enqueue of the same logical message is
    a no-op, so a message is never queued (and thus never delivered) twice.
    """
    if session is not None:
        await _enqueue(session, msg)
        return
    async with session_scope() as scope:
        await _enqueue(scope, msg)


async def _enqueue(session: AsyncSession, msg: Notification) -> None:
    stmt = (
        pg_insert(Outbox)
        .values(
            channel=msg.channel.value,
            dedup_key=msg.dedup_key,
            payload=serialize_notification(msg),
            status=OutboxStatus.PENDING,
        )
        .on_conflict_do_nothing(constraint="uq_outbox_dedup")
    )
    await session.execute(stmt)


async def dispatch_pending(
    transports: Mapping[Channel, Transport],
    *,
    max_batch: int = DEFAULT_MAX_BATCH,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    session: AsyncSession | None = None,
) -> DispatchStats:
    """Drain PENDING rows oldest-first and deliver via the matching transport."""
    if session is not None:
        return await _dispatch_pending(
            session, transports, max_batch=max_batch, max_attempts=max_attempts
        )
    async with session_scope() as scope:
        return await _dispatch_pending(
            scope, transports, max_batch=max_batch, max_attempts=max_attempts
        )


async def _dispatch_pending(
    session: AsyncSession,
    transports: Mapping[Channel, Transport],
    *,
    max_batch: int,
    max_attempts: int,
) -> DispatchStats:
    stmt = (
        select(Outbox)
        .where(Outbox.status == OutboxStatus.PENDING)
        .order_by(Outbox.created_at.asc(), Outbox.id.asc())
        .limit(max_batch)
        .with_for_update(skip_locked=True)
    )
    rows = (await session.execute(stmt)).scalars().all()

    stats = DispatchStats()
    for row in rows:
        channel = Channel(row.channel)
        transport = transports.get(channel)
        if transport is None:
            stats.skipped += 1
            _log.warning(
                "outbox.dispatch.no_transport",
                channel=row.channel,
                dedup_key=row.dedup_key,
            )
            continue

        stats.processed += 1
        msg = deserialize_notification(channel, row.dedup_key, row.payload)
        result = await transport.send(msg)
        next_state = decide_next_state(
            current_attempts=row.attempts, result=result, max_attempts=max_attempts
        )

        row.status = next_state.status
        row.attempts = next_state.attempts
        row.last_error = next_state.last_error
        if next_state.delivered:
            row.message_id = next_state.message_id
            row.sent_at = datetime.now(UTC)
            stats.sent += 1
        elif next_state.status is OutboxStatus.PENDING:
            stats.retried += 1
        else:
            stats.failed += 1

    return stats
