from __future__ import annotations

import pytest

from gamer.config import Settings, get_settings


def test_defaults_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("GAMER_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    s = Settings()
    assert s.env == "dev"
    assert s.db.host == "localhost"
    assert s.twitch.enabled is False


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_DB__HOST", "db.internal")
    monkeypatch.setenv("GAMER_DB__PASSWORD", "hunter2")
    get_settings.cache_clear()
    s = Settings()
    assert s.db.host == "db.internal"
    assert s.db.password.get_secret_value() == "hunter2"


def test_secret_never_leaks_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_TELEGRAM__BOT_TOKEN", "123:SECRETTOKEN")
    get_settings.cache_clear()
    s = Settings()
    assert "SECRETTOKEN" not in repr(s)
    assert "SECRETTOKEN" not in str(s.telegram)
    assert s.telegram.bot_token.get_secret_value() == "123:SECRETTOKEN"


def test_dsn_builds_expected_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_DB__PASSWORD", "pw")
    get_settings.cache_clear()
    s = Settings()
    assert s.db.dsn(driver="asyncpg").startswith("postgresql+asyncpg://")
    assert s.db.dsn(driver="psycopg").startswith("postgresql+psycopg://")
    assert "pw" in s.db.dsn()


def test_twitch_enabled_when_both_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_TWITCH__CLIENT_ID", "id")
    monkeypatch.setenv("GAMER_TWITCH__CLIENT_SECRET", "secret")
    get_settings.cache_clear()
    assert Settings().twitch.enabled is True
