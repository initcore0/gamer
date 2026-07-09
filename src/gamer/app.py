"""Application lifecycle — wires config, logging, DB, scheduler.

M0: boots an empty app (scheduler running, no jobs yet) and shuts down cleanly.
Milestones register their jobs and the bot here.
"""

from __future__ import annotations

import asyncio
import signal

from sqlalchemy import text

from gamer.config import get_settings
from gamer.db import dispose_engine, get_engine
from gamer.logging import configure_logging, get_logger
from gamer.scheduler import Scheduler

log = get_logger("app")


class App:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.scheduler = Scheduler()
        self._stop = asyncio.Event()

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

        # M1+ register source-poll jobs here via self.scheduler.add_interval_job(...).
        self.scheduler.start()

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
        await dispose_engine()
        log.info("app_stopped")


async def main_async() -> None:
    await App().run()
