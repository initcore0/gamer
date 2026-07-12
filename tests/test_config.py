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


def test_rss_feeds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("GAMER_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    s = Settings()
    assert s.rss.enabled is True
    assert len(s.rss.feeds) == 3


def test_rss_feeds_csv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_RSS__FEEDS", "https://a.com, https://b.com ,")
    get_settings.cache_clear()
    assert Settings().rss.feeds == ["https://a.com", "https://b.com"]


def test_telegram_allowed_chat_ids_default_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("GAMER_"):
            monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    # Empty by default => the bot is open to everyone.
    assert Settings().telegram.allowed_chat_ids == []


def test_telegram_allowed_chat_ids_csv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # CSV of ints, tolerant of whitespace + a trailing comma; groups are negative.
    monkeypatch.setenv("GAMER_TELEGRAM__ALLOWED_CHAT_IDS", " 111, -222 ,333,")
    get_settings.cache_clear()
    assert Settings().telegram.allowed_chat_ids == [111, -222, 333]


def test_health_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_HEALTH__STALE_AFTER_HOURS", "12")
    get_settings.cache_clear()
    assert Settings().health.stale_after_hours == 12
    assert Settings().health.api_port == 8080


def test_ui_public_base_url_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    assert Settings().ui.public_base_url == ""  # deep links disabled by default
    monkeypatch.setenv("GAMER_UI__PUBLIC_BASE_URL", "https://gamer.example.com")
    get_settings.cache_clear()
    assert Settings().ui.public_base_url == "https://gamer.example.com"


def test_llm_openai_backend_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAMER_LLM__API", "openai")
    monkeypatch.setenv("GAMER_LLM__OPENAI_BASE_URL", "http://box:8080/v1")
    monkeypatch.setenv("GAMER_LLM__OPENAI_API_KEY", "sk-supersecret-xyz")
    get_settings.cache_clear()
    s = Settings()
    assert s.llm.api == "openai"
    assert s.llm.openai_base_url == "http://box:8080/v1"
    assert s.llm.openai_api_key.get_secret_value() == "sk-supersecret-xyz"
    assert "sk-supersecret-xyz" not in repr(s.llm)  # secret masked
