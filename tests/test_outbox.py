"""Outbox tests.

Pure serialization / retry-decision / dedup logic is tested WITHOUT a DB using a
fake in-memory transport. Full enqueue + dispatch-against-Postgres flows are
marked ``@pytest.mark.integration`` (deselected in the default unit run).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from gamer.db.models import OutboxStatus
from gamer.notify import (
    Button,
    Channel,
    DeliveryResult,
    Notification,
    Transport,
    decide_next_state,
    deserialize_notification,
    serialize_notification,
)

# ── Fake transport ────────────────────────────────────────────────────────────


@dataclass
class FakeTransport:
    """In-memory :class:`Transport` for tests; records sends, scripts failures."""

    channel: Channel = Channel.TELEGRAM_DM
    fail_retryable: bool = False
    fail_permanent: bool = False
    sent: list[Notification] = field(default_factory=list)
    _next_id: int = 100

    async def send(self, msg: Notification) -> DeliveryResult:
        self.sent.append(msg)
        if self.fail_retryable:
            return DeliveryResult(ok=False, error="rate limited", retryable=True)
        if self.fail_permanent:
            return DeliveryResult(ok=False, error="bad request", retryable=False)
        self._next_id += 1
        return DeliveryResult(ok=True, message_id=str(self._next_id))


def test_fake_transport_satisfies_protocol() -> None:
    t: Transport = FakeTransport()
    assert isinstance(t, Transport)


# ── Serialization round-trip ──────────────────────────────────────────────────


def _msg(**overrides: object) -> Notification:
    base = dict(
        channel=Channel.TELEGRAM_DM,
        text="Play Hades",
        dedup_key="rec:1",
        buttons=[Button(text="👍", action="feedback:up:1")],
        meta={"parse_mode": "HTML"},
    )
    base.update(overrides)
    return Notification(**base)  # type: ignore[arg-type]


def test_serialize_shape() -> None:
    payload = serialize_notification(_msg())
    assert payload == {
        "text": "Play Hades",
        "buttons": [{"text": "👍", "action": "feedback:up:1"}],
        "meta": {"parse_mode": "HTML"},
        # multi-user: per-message target chat override (None => transport default).
        "target_chat_id": None,
    }
    # channel / dedup_key are columns, not payload.
    assert "channel" not in payload
    assert "dedup_key" not in payload


def test_serialize_deserialize_round_trip() -> None:
    original = _msg()
    payload = serialize_notification(original)
    rebuilt = deserialize_notification(original.channel, original.dedup_key, payload)
    assert rebuilt.channel == original.channel
    assert rebuilt.text == original.text
    assert rebuilt.dedup_key == original.dedup_key
    assert rebuilt.meta == original.meta
    assert [(b.text, b.action) for b in rebuilt.buttons] == [
        (b.text, b.action) for b in original.buttons
    ]


def test_deserialize_no_buttons() -> None:
    rebuilt = deserialize_notification(
        Channel.TELEGRAM_GROUP, "digest:2026-07-09", {"text": "movers"}
    )
    assert rebuilt.buttons == []
    assert rebuilt.meta == {}


# ── Retry / dedup decision logic ──────────────────────────────────────────────


def test_decide_success() -> None:
    ns = decide_next_state(current_attempts=0, result=DeliveryResult(ok=True, message_id="42"))
    assert ns.status is OutboxStatus.SENT
    assert ns.delivered is True
    assert ns.message_id == "42"
    assert ns.attempts == 1
    assert ns.last_error is None


def test_decide_retryable_keeps_pending() -> None:
    ns = decide_next_state(
        current_attempts=0,
        result=DeliveryResult(ok=False, error="rate limited", retryable=True),
        max_attempts=5,
    )
    assert ns.status is OutboxStatus.PENDING
    assert ns.delivered is False
    assert ns.attempts == 1
    assert ns.last_error == "rate limited"
    assert ns.message_id is None


def test_decide_retryable_exhausts_to_failed() -> None:
    # 4 attempts already made; this 5th failure hits max_attempts=5.
    ns = decide_next_state(
        current_attempts=4,
        result=DeliveryResult(ok=False, error="rate limited", retryable=True),
        max_attempts=5,
    )
    assert ns.attempts == 5
    assert ns.status is OutboxStatus.FAILED
    assert ns.delivered is False


def test_decide_permanent_failure_is_failed_immediately() -> None:
    ns = decide_next_state(
        current_attempts=0,
        result=DeliveryResult(ok=False, error="bad request", retryable=False),
        max_attempts=5,
    )
    assert ns.status is OutboxStatus.FAILED
    assert ns.attempts == 1
    assert ns.last_error == "bad request"


@pytest.mark.parametrize(
    ("attempts", "expected"),
    [
        (0, OutboxStatus.PENDING),
        (1, OutboxStatus.PENDING),
        (2, OutboxStatus.FAILED),  # attempts -> 3 == max_attempts=3
    ],
)
def test_decide_retry_boundary(attempts: int, expected: OutboxStatus) -> None:
    ns = decide_next_state(
        current_attempts=attempts,
        result=DeliveryResult(ok=False, error="x", retryable=True),
        max_attempts=3,
    )
    assert ns.status is expected


# ── Fake-transport driven "dispatch" without a DB ─────────────────────────────


async def test_fake_transport_success_flow() -> None:
    """Emulate the dispatch decision loop against the fake transport, no DB."""
    transport = FakeTransport()
    msg = _msg()
    result = await transport.send(msg)
    ns = decide_next_state(current_attempts=0, result=result)
    assert transport.sent == [msg]
    assert ns.status is OutboxStatus.SENT
    assert ns.message_id is not None


async def test_fake_transport_retryable_flow() -> None:
    transport = FakeTransport(fail_retryable=True)
    result = await transport.send(_msg())
    ns = decide_next_state(current_attempts=0, result=result, max_attempts=5)
    assert ns.status is OutboxStatus.PENDING
    assert ns.attempts == 1


async def test_fake_transport_permanent_flow() -> None:
    transport = FakeTransport(fail_permanent=True)
    result = await transport.send(_msg())
    ns = decide_next_state(current_attempts=0, result=result)
    assert ns.status is OutboxStatus.FAILED


# ── Integration: real Postgres round-trips ────────────────────────────────────


@pytest.mark.integration
async def test_enqueue_dedup_and_dispatch() -> None:
    """Enqueue is idempotent on (channel, dedup_key); dispatch sends once.

    Requires a live database configured via ``GAMER_DB__*`` env vars.
    """
    from sqlalchemy import func, select

    from gamer.db import session_scope
    from gamer.db.models import Outbox
    from gamer.notify import dispatch_pending, enqueue

    msg = _msg(dedup_key="integration:rec:1")

    async with session_scope() as session:
        await enqueue(msg, session=session)
        await enqueue(msg, session=session)  # duplicate -> no-op
        count = await session.scalar(
            select(func.count())
            .select_from(Outbox)
            .where(Outbox.channel == msg.channel.value)
            .where(Outbox.dedup_key == msg.dedup_key)
        )
        assert count == 1

    transport = FakeTransport()
    async with session_scope() as session:
        stats = await dispatch_pending({Channel.TELEGRAM_DM: transport}, session=session)
    assert stats.sent >= 1
    assert len(transport.sent) >= 1

    async with session_scope() as session:
        row = await session.scalar(
            select(Outbox)
            .where(Outbox.channel == msg.channel.value)
            .where(Outbox.dedup_key == msg.dedup_key)
        )
        assert row is not None
        assert row.status is OutboxStatus.SENT
        assert row.message_id is not None
        assert row.sent_at is not None
