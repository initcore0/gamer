from __future__ import annotations

from gamer.config import Settings
from gamer.jobs import register_jobs
from gamer.scheduler import Scheduler
from gamer.sources import REGISTRY


class _RecordingScheduler(Scheduler):
    def __init__(self) -> None:
        super().__init__()
        self.registered: list[tuple[str, int]] = []

    def add_interval_job(self, fn, *, seconds: int, name: str) -> None:  # type: ignore[override]
        self.registered.append((name, seconds))


def test_register_jobs_wires_every_source() -> None:
    sched = _RecordingScheduler()
    # No group chat id → digest disabled, but all source polls must register.
    register_jobs(sched, Settings())
    names = {n for n, _ in sched.registered}
    for source_name in REGISTRY:
        assert f"poll:{source_name}" in names
    assert "digest" not in names  # gated on group_chat_id
    # UI-M3: the hourly rollup-writer job is always registered.
    assert ("rollups:refresh", 3600) in sched.registered


def test_digest_registered_when_group_configured(monkeypatch) -> None:
    monkeypatch.setenv("GAMER_TELEGRAM__GROUP_CHAT_ID", "-100123")
    sched = _RecordingScheduler()
    register_jobs(sched, Settings())
    assert "digest" in {n for n, _ in sched.registered}
