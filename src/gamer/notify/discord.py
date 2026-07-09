"""Discord webhook transport (M5) — proves the :class:`Transport` abstraction.

A read-only broadcast channel: a :class:`Notification` is POSTed to a Discord
webhook URL as ``{"content": ...}``. Discord speaks **Markdown**, not HTML, so
the digest's ``<b>``/``<i>``/``<a href>`` markup is converted to Discord markdown
by the pure :func:`html_to_discord_markdown` helper before sending.

The webhook URL embeds a secret token, so it is a :class:`~pydantic.SecretStr`.
It is unwrapped only to build the request and is *never* logged: error strings
that could echo it pass through :func:`~gamer.logging.redact_secrets`, and the
URL is otherwise kept out of log events entirely.
"""

from __future__ import annotations

import html as _html
import re

import httpx

from gamer.config import Settings, get_settings
from gamer.logging import get_logger, redact_secrets
from gamer.notify.base import Channel, DeliveryResult, Notification
from gamer.sources.http import PoliteClient, RetryableStatus

_log = get_logger(__name__)

#: Discord rejects messages whose ``content`` exceeds 2000 characters.
DISCORD_CONTENT_LIMIT = 2000

# Discord is polite-friendly but conservative; a webhook is low-volume.
_RATE = 5
_PER = 1.0

_BOLD_RE = re.compile(r"<\s*/?\s*b\s*>", re.IGNORECASE)
_ITALIC_RE = re.compile(r"<\s*/?\s*i\s*>", re.IGNORECASE)
_LINK_RE = re.compile(
    r"<\s*a\s+[^>]*?href\s*=\s*(['\"])(?P<url>.*?)\1[^>]*>(?P<text>.*?)<\s*/\s*a\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def html_to_discord_markdown(text: str) -> str:
    """Convert the digest's minimal HTML markup to Discord markdown.

    Our notifications only ever use ``<b>``, ``<i>`` and ``<a href="…">…</a>``
    (see :mod:`gamer.notify.digest`). Map them to Discord's ``**bold**``,
    ``*italic*`` and ``[text](url)``, drop any other stray tags, and unescape
    HTML entities (``&amp;`` → ``&``) last so entities inside link text survive.
    Pure and side-effect free, so it can be unit-tested in isolation.
    """

    # Links first: capture href + inner text before the generic tag strip.
    def _link(match: re.Match[str]) -> str:
        url = match.group("url").strip()
        label = _ANY_TAG_RE.sub("", match.group("text")).strip()
        return f"[{label}]({url})"

    out = _LINK_RE.sub(_link, text)
    out = _BOLD_RE.sub("**", out)
    out = _ITALIC_RE.sub("*", out)
    # Anything left (unexpected tags) is stripped so no raw HTML leaks through.
    out = _ANY_TAG_RE.sub("", out)
    return _html.unescape(out)


def _to_discord_content(msg: Notification) -> str:
    """Render a notification's text as Discord-ready, length-capped content."""
    content = html_to_discord_markdown(msg.text)
    if len(content) > DISCORD_CONTENT_LIMIT:
        # Truncate on a character boundary and mark the cut so it's obviously clipped.
        content = content[: DISCORD_CONTENT_LIMIT - 1].rstrip() + "…"
    return content


class DiscordWebhook:
    """Read-only Discord webhook transport.

    Sends a notification's text (converted to Discord markdown) to a webhook URL.
    Buttons are ignored — a webhook broadcast has no interactive callbacks. The
    transport owns a :class:`~gamer.sources.http.PoliteClient`; call :meth:`aclose`
    (or use it as an async context manager) to release the HTTP connection.
    """

    channel = Channel.DISCORD

    def __init__(self, *, webhook_url: str, client: PoliteClient | None = None) -> None:
        # Stored only to make the request; never logged or exposed in repr.
        self._webhook_url = webhook_url
        self._client = client or PoliteClient(rate=_RATE, per=_PER)

    def __repr__(self) -> str:  # pragma: no cover - trivial, but keeps the URL out
        return f"<{type(self).__name__} channel={self.channel.value}>"

    async def send(self, msg: Notification) -> DeliveryResult:
        content = _to_discord_content(msg)
        try:
            resp = await self._client.request("POST", self._webhook_url, json={"content": content})
        except RetryableStatus:
            # PoliteClient exhausted its retries on 429/5xx — still transient.
            _log.warning(
                "discord.send.retryable_exhausted",
                channel=self.channel.value,
                dedup_key=msg.dedup_key,
            )
            return DeliveryResult(ok=False, error="retryable status exhausted", retryable=True)
        except httpx.HTTPError as exc:
            # Network/transport error: assume transient. Redact in case the URL leaks.
            _log.warning(
                "discord.send.transport_error",
                channel=self.channel.value,
                dedup_key=msg.dedup_key,
                error=redact_secrets(type(exc).__name__),
            )
            return DeliveryResult(ok=False, error=type(exc).__name__, retryable=True)

        return self._interpret(resp, msg)

    def _interpret(self, resp: httpx.Response, msg: Notification) -> DeliveryResult:
        status = resp.status_code
        # 204 (or any 2xx) → delivered. Discord returns no id unless ?wait=true.
        if 200 <= status < 300:
            message_id = self._message_id(resp)
            return DeliveryResult(ok=True, message_id=message_id)

        # PoliteClient only reaches here for statuses it does NOT retry: i.e. 4xx.
        # 429 is normally retried internally, but handle it defensively anyway.
        if status == 429:
            retry_after = self._retry_after(resp)
            _log.warning(
                "discord.send.rate_limited",
                channel=self.channel.value,
                dedup_key=msg.dedup_key,
                retry_after=retry_after,
            )
            return DeliveryResult(ok=False, error="rate limited", retryable=True)

        # Other 4xx (bad webhook, malformed content…) — retrying won't help.
        # Never log the response body; it can echo the request. Log status only.
        _log.error(
            "discord.send.client_error",
            channel=self.channel.value,
            dedup_key=msg.dedup_key,
            status=status,
        )
        return DeliveryResult(ok=False, error=f"http {status}", retryable=False)

    @staticmethod
    def _message_id(resp: httpx.Response) -> str | None:
        """Extract the message id when Discord echoes one (``?wait=true``)."""
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        mid = data.get("id") if isinstance(data, dict) else None
        return str(mid) if mid is not None else None

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        """Discord's JSON ``retry_after`` (seconds), falling back to the header."""
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("retry_after"), int | float):
            return float(data["retry_after"])
        header = resp.headers.get("Retry-After")
        if header:
            try:
                return float(header)
            except ValueError:
                return None
        return None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> DiscordWebhook:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def build_discord_transport(settings: Settings | None = None) -> DiscordWebhook | None:
    """Build the Discord transport, or ``None`` when the webhook isn't configured.

    The URL is unwrapped from its :class:`~pydantic.SecretStr` here and handed to
    the transport, which keeps it out of logs and ``repr``.
    """
    settings = settings or get_settings()
    if not settings.discord.enabled:
        return None
    url = settings.discord.webhook_url.get_secret_value()
    return DiscordWebhook(webhook_url=url)
