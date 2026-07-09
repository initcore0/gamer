"""Application lifecycle — wires config, logging, DB, scheduler, bot.

Boots the ingestion scheduler (source polls + daily digest) and, when a Telegram
bot token is configured, the aiogram long-poller — then shuts both down cleanly.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from sqlalchemy import text

from gamer.api import run_api
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
        self._api_task: asyncio.Task[None] | None = None

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

        # Read-only status API (the public build log). A taken port must not
        # crash the app, so the task guards itself and logs on failure.
        self._api_task = asyncio.create_task(self._run_api())
        log.info("api_enabled", port=self.settings.health.api_port)

        self._install_signal_handlers()
        log.info("app_ready")
        await self._stop.wait()

        await self.shutdown()

    async def _run_api(self) -> None:
        """Serve the status API, guarding so a bind failure (e.g. port taken)
        logs and returns instead of taking down the whole app."""
        try:
            await run_api(self.settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("api_failed", error=f"{type(exc).__name__}: {exc}")

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
        if self._api_task is not None:
            self._api_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._api_task
        await dispose_engine()
        log.info("app_stopped")


async def main_async() -> None:
    await App().run()
