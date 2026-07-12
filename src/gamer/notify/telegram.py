"""Telegram transports (PLAN.md §4.6 / §4.7).

Two channels over one bot:

* :class:`TelegramDM` — the streamer's private chat. Interactive: inline
  buttons (👍/👎/"played it") are attached from :attr:`Notification.buttons`,
  mapping each :attr:`Button.action` onto the button's ``callback_data`` so the
  bot can route feedback back into ``fit``.
* :class:`TelegramGroup` — the broadcast digest. Read-only: buttons are ignored.

The bot token is a :class:`~pydantic.SecretStr`; it is unwrapped only to build
the :class:`aiogram.Bot` and is *never* logged. Errors are logged by class name
and the transport's own message, never by echoing the aiogram exception's full
string (which can embed request context).
"""

from __future__ import annotations

from collections.abc import Sequence

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from gamer.config import Settings, get_settings
from gamer.logging import get_logger
from gamer.notify.base import Button, Channel, DeliveryResult, Notification

_log = get_logger(__name__)


def build_inline_keyboard(buttons: Sequence[Button]) -> InlineKeyboardMarkup | None:
    """Map :class:`Button` actions onto an inline keyboard (one button per row).

    ``Button.action`` becomes the button's ``callback_data`` so the bot can parse
    it back (e.g. ``"feedback:up:<rec_id>"``). Returns ``None`` when there are no
    buttons so aiogram omits ``reply_markup`` entirely.
    """
    if not buttons:
        return None
    rows = [[InlineKeyboardButton(text=b.text, callback_data=b.action)] for b in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


class _TelegramTransport:
    """Shared Telegram send logic for a single chat.

    Subclasses fix :attr:`channel`, the target ``chat_id``, and whether buttons
    are honored. Instances own an :class:`aiogram.Bot`; call :meth:`aclose` (or
    use as an async context manager) to release its HTTP session.
    """

    channel: Channel
    _interactive: bool = False

    def __init__(self, *, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def send(self, msg: Notification) -> DeliveryResult:
        reply_markup = build_inline_keyboard(msg.buttons) if self._interactive else None
        parse_mode = msg.meta.get("parse_mode")
        disable_preview = msg.meta.get("disable_preview")
        # Per-message target override (multi-user DM fan-out); else our own chat.
        chat_id = msg.target_chat_id if msg.target_chat_id is not None else self._chat_id
        try:
            sent = await self._bot.send_message(
                chat_id=chat_id,
                text=msg.text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_preview,
            )
        except TelegramRetryAfter as exc:
            # Rate limited — the message is fine, just try again later.
            _log.warning(
                "telegram.send.rate_limited",
                channel=self.channel.value,
                dedup_key=msg.dedup_key,
                retry_after=exc.retry_after,
            )
            return DeliveryResult(
                ok=False,
                error=f"rate limited, retry after {exc.retry_after}s",
                retryable=True,
            )
        except TelegramBadRequest as exc:
            # Malformed request (bad chat, bad markup…) — retrying won't help.
            _log.error(
                "telegram.send.bad_request",
                channel=self.channel.value,
                dedup_key=msg.dedup_key,
                error=exc.message,
            )
            return DeliveryResult(ok=False, error=exc.message, retryable=False)
        except TelegramAPIError as exc:
            # Other API/network errors (5xx, connectivity): assume transient.
            _log.warning(
                "telegram.send.api_error",
                channel=self.channel.value,
                dedup_key=msg.dedup_key,
                error=type(exc).__name__,
            )
            return DeliveryResult(ok=False, error=type(exc).__name__, retryable=True)

        return DeliveryResult(ok=True, message_id=str(sent.message_id))

    async def aclose(self) -> None:
        await self._bot.session.close()

    async def __aenter__(self) -> _TelegramTransport:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class TelegramDM(_TelegramTransport):
    """Interactive DM to the streamer (buttons honored)."""

    channel = Channel.TELEGRAM_DM
    _interactive = True

    def __init__(self, *, bot: Bot, chat_id: int) -> None:
        super().__init__(bot=bot, chat_id=chat_id)


class TelegramGroup(_TelegramTransport):
    """Read-only broadcast to the group (buttons ignored)."""

    channel = Channel.TELEGRAM_GROUP
    _interactive = False

    def __init__(self, *, bot: Bot, chat_id: int) -> None:
        super().__init__(bot=bot, chat_id=chat_id)


def build_bot(settings: Settings | None = None) -> Bot:
    """Construct the shared :class:`aiogram.Bot` from settings.

    The token is unwrapped from its :class:`~pydantic.SecretStr` here and nowhere
    else, so it stays out of logs and reprs.
    """
    settings = settings or get_settings()
    return Bot(token=settings.telegram.bot_token.get_secret_value())


def build_telegram_transports(
    settings: Settings | None = None, *, bot: Bot | None = None
) -> dict[Channel, _TelegramTransport]:
    """Build both Telegram transports sharing one bot, keyed by channel."""
    settings = settings or get_settings()
    bot = bot or build_bot(settings)
    return {
        Channel.TELEGRAM_DM: TelegramDM(bot=bot, chat_id=settings.telegram.dm_chat_id),
        Channel.TELEGRAM_GROUP: TelegramGroup(bot=bot, chat_id=settings.telegram.group_chat_id),
    }
