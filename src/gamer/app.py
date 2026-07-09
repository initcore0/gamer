"""Application lifecycle — wires config, logging, DB, scheduler, bot.

Boots the ingestion scheduler (source polls + daily digest) and, when a Telegram
bot token is configured, the aiogram long-poller — then shuts both down cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from sqlalchemy import text

from gamer.bot.app import run_bot
from gamer.config import get_settings
from gamer.db import dispose_engine, get_engine
from gamer.jobs import register_jobs
from gamer.logging import configure_logging, get_logger
from gamer.scheduler import Scheduler

log = get_logger("app")


class App:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.scheduler = Scheduler()
        self._stop = asyncio.Event()
        self._bot_task: asyncio.Task[None] | None = None

    async def _check_db(self) -> None:
        """Fail fast if the database is unreachable at boot."""
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("db_connected", host=self.settings.db.host, name=self.settings.db.name)

    async def run(self) -> None:
        configure_logging(level=self.settings.log_level, json=self.settings.log_json)
        log.info("app_starting", env=self.settings.env, version="0.1.0")

        await self._check_db()

        register_jobs(self.scheduler, self.settings)
        self.scheduler.start()

        # Start the Telegram bot poller only when a token is configured, so the
        # ingestion pipeline can run headless (e.g. in CI / early build-in-public).
        if self.settings.telegram.bot_token.get_secret_value():
            self._bot_task = asyncio.create_task(run_bot())
            log.info("bot_enabled")
        else:
            log.info("bot_disabled", reason="no bot_token configured")

        self._install_signal_handlers()
        log.info("app_ready")
        await self._stop.wait()

        await self.shutdown()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:  # e.g. Windows
                pass

    async def shutdown(self) -> None:
        log.info("app_stopping")
        self.scheduler.shutdown()
        if self._bot_task is not None:
            self._bot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._bot_task
        await dispose_engine()
        log.info("app_stopped")


async def main_async() -> None:
    await App().run()
