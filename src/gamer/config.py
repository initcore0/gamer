"""Application configuration.

Config comes from environment variables ONLY — no secrets in files (PLAN.md §5).
Nested settings use the ``GAMER_`` prefix with ``__`` as the delimiter, e.g.
``GAMER_DB__PASSWORD``. Every credential is a :class:`~pydantic.SecretStr` so it
never renders in logs or ``repr()``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection. Password is local-only but still a secret."""

    host: str = "localhost"
    port: int = 5432
    user: str = "gamer"
    password: SecretStr = SecretStr("")
    name: str = "gamer"

    def dsn(self, *, driver: str = "asyncpg") -> str:
        """SQLAlchemy DSN. ``driver=asyncpg`` for the app, ``psycopg`` for alembic."""
        scheme = "postgresql+asyncpg" if driver == "asyncpg" else "postgresql+psycopg"
        return (
            f"{scheme}://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class SteamSettings(BaseSettings):
    api_key: SecretStr = SecretStr("")


class TelegramSettings(BaseSettings):
    bot_token: SecretStr = SecretStr("")
    dm_chat_id: int = 0
    group_chat_id: int = 0


class TwitchSettings(BaseSettings):
    client_id: SecretStr = SecretStr("")
    client_secret: SecretStr = SecretStr("")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id.get_secret_value() and self.client_secret.get_secret_value())


class EmbeddingsSettings(BaseSettings):
    enabled: bool = False
    model: str = "BAAI/bge-small-en-v1.5"
    # Dimensionality of the configured model; must match the pgvector column.
    dim: int = 384


class LLMSettings(BaseSettings):
    enabled: bool = False
    # Which backend the endpoint speaks: Ollama's native /api/generate, or an
    # OpenAI-compatible /chat/completions (llama.cpp, vLLM, LM Studio, …).
    api: Literal["ollama", "openai"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    # OpenAI-compatible base URL (must include the /v1). Only used when api="openai".
    openai_base_url: str = "http://localhost:8080/v1"
    # Optional bearer key for the OpenAI-compatible server (llama.cpp usually none).
    openai_api_key: SecretStr = SecretStr("")


class RssSettings(BaseSettings):
    """Broader-news RSS feeds (PLAN.md §3). Pluggable list of feed URLs."""

    enabled: bool = True
    # Comma-separated in the env var (GAMER_RSS__FEEDS=url1,url2). NoDecode stops
    # pydantic-settings from JSON-parsing it first, so our validator splits the CSV.
    feeds: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "https://www.pcgamer.com/rss/",
            "https://www.rockpapershotgun.com/feed",
            "https://www.eurogamer.net/feed",
        ]
    )

    @field_validator("feeds", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


class HealthSettings(BaseSettings):
    """Self-reporting / alerting (PLAN.md §6 M4). A source silent longer than
    ``stale_after_hours`` pings the streamer once."""

    stale_after_hours: int = 24
    # Status page bind (read-only public build log).
    api_host: str = "0.0.0.0"
    api_port: int = 8080


class DiscordSettings(BaseSettings):
    """Discord webhook transport (M5) — proves the Transport abstraction."""

    webhook_url: SecretStr = SecretStr("")

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url.get_secret_value())


class SwitchSettings(BaseSettings):
    """Switch eShop release feed (M5) — proves the platform abstraction."""

    enabled: bool = False
    # A free public feed of Switch eShop releases (JSON/RSS). Kept configurable.
    feed_url: str = ""


class Settings(BaseSettings):
    """Top-level settings. Instantiated once via :func:`get_settings`."""

    model_config = SettingsConfigDict(
        env_prefix="GAMER_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = False

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    steam: SteamSettings = Field(default_factory=SteamSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    twitch: TwitchSettings = Field(default_factory=TwitchSettings)
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    rss: RssSettings = Field(default_factory=RssSettings)
    health: HealthSettings = Field(default_factory=HealthSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    switch: SwitchSettings = Field(default_factory=SwitchSettings)


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Tests can clear the cache via ``get_settings.cache_clear()``."""
    return Settings()
