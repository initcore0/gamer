from __future__ import annotations

from gamer.config import Settings
from gamer.jobs import register_jobs
from gamer.scheduler import Scheduler
from gamer.sources import REGISTRY


class _RecordingScheduler(Scheduler):
    def __init__(self) -> None:
        super().__init__()
        self.registered: list[tuple[str, int]] = []
        self.daily: list[tuple[str, int, int]] = []

    def add_interval_job(self, fn, *, seconds: int, name: str) -> None:  # type: ignore[override]
        self.registered.append((name, seconds))

    def add_daily_job(self, fn, *, hour: int, minute: int = 0, name: str) -> None:  # type: ignore[override]
        self.daily.append((name, hour, minute))


def test_register_jobs_wires_every_source() -> None:
    sched = _RecordingScheduler()
    # No group chat id → digest disabled, but all source polls must register.
    register_jobs(sched, Settings())
    names = {n for n, _ in sched.registered}
    for source_name in REGISTRY:
        assert f"poll:{source_name}" in names
    assert "digest" not in {n for n, _, _ in sched.daily}  # gated on group_chat_id
    # UI-M3: the hourly rollup-writer job is always registered.
    assert ("rollups:refresh", 3600) in sched.registered
    # M7: the hourly genre-subscription auto-track job is always registered.
    assert ("genre:track", 3600) in sched.registered


def test_digest_registered_when_group_configured(monkeypatch) -> None:
    monkeypatch.setenv("GAMER_TELEGRAM__GROUP_CHAT_ID", "-100123")
    sched = _RecordingScheduler()
    register_jobs(sched, Settings())
    # Cron (fixed daily time), not an interval — a restart must not drift the digest.
    assert ("digest", 16, 0) in sched.daily
    assert "digest" not in {n for n, _ in sched.registered}


def test_digest_hour_configurable(monkeypatch) -> None:
    monkeypatch.setenv("GAMER_TELEGRAM__GROUP_CHAT_ID", "-100123")
    monkeypatch.setenv("GAMER_TELEGRAM__DIGEST_HOUR_UTC", "7")
    sched = _RecordingScheduler()
    register_jobs(sched, Settings())
    assert ("digest", 7, 0) in sched.daily
