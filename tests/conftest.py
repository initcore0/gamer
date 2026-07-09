from __future__ import annotations

import os

import pytest

from gamer.config import get_settings


@pytest.fixture(autouse=True)
def _clean_settings_cache() -> None:
    """Ensure each test builds Settings fresh from the current environment."""
    get_settings.cache_clear()


@pytest.fixture
def steam_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_STEAM__API_KEY", "DEADBEEFDEADBEEFDEADBEEFDEADBEEF")
    monkeypatch.setenv("GAMER_DB__PASSWORD", "test-pw")
    get_settings.cache_clear()
    assert "GAMER_STEAM__API_KEY" in os.environ
