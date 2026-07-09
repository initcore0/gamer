"""Bot runtime — constructs the aiogram Bot + Dispatcher and starts polling.

Kept separate from handlers so the command logic stays unit-testable without a
live Telegram connection.
"""

from __future__ import annotations

from aiogram import Bot, Dispatcher

from gamer.bot.handlers import router
from gamer.config import get_settings
from gamer.logging import get_logger

log = get_logger("bot.app")


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    return dp


def build_bot() -> Bot:
    token = get_settings().telegram.bot_token.get_secret_value()
    if not token:
        raise RuntimeError("GAMER_TELEGRAM__BOT_TOKEN is not set")
    return Bot(token=token)


async def run_bot() -> None:
    """Start long-polling. Blocks until cancelled."""
    bot = build_bot()
    dp = build_dispatcher()
    log.info("bot_polling_start")
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        await bot.session.close()
        log.info("bot_polling_stopped")
