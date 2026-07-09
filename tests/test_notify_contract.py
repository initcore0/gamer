from __future__ import annotations

from gamer.notify import (
    Button,
    Channel,
    DeliveryResult,
    Notification,
    Transport,
)


class _EchoTransport:
    channel = Channel.TELEGRAM_DM

    async def send(self, msg: Notification) -> DeliveryResult:
        return DeliveryResult(ok=True, message_id="1")


def test_echo_transport_satisfies_protocol() -> None:
    t: Transport = _EchoTransport()
    assert isinstance(t, Transport)


async def test_notification_carries_buttons() -> None:
    msg = Notification(
        channel=Channel.TELEGRAM_DM,
        text="Play Hades",
        dedup_key="rec:1",
        buttons=[Button(text="👍", action="feedback:up:1")],
    )
    result = await _EchoTransport().send(msg)
    assert result.ok
    assert msg.buttons[0].action == "feedback:up:1"
