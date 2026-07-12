"""Application configuration.

Config comes from environment variables ONLY — no secrets in files (PLAN.md §5).
Nested settings use the ``GAMER_`` prefix with ``__`` as the delimiter, e.g.
``GAMER_DB__PASSWORD``. Every credential is a :class:`~pydantic.SecretStr` so it
never renders in logs or ``repr()``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    """PostgreSQL connection. Password is local-only but still a secret."""

    host: str = "localhost"
    port: int = 5432
    user: str = "gamer"
    password: SecretStr = SecretStr("")
    name: str = "gamer"

    def dsn(self, *, driver: str = "asyncpg") -> str:
        """SQLAlchemy DSN. ``driver=asyncpg`` for the app, ``psycopg`` for alembic.

        User and password are percent-encoded so credentials containing URL-
        reserved characters (``@ : / %`` — common in generated passwords) don't
        corrupt the DSN or silently decode to a different value.
        """
        scheme = "postgresql+asyncpg" if driver == "asyncpg" else "postgresql+psycopg"
        user = quote(self.user, safe="")
        password = quote(self.password.get_secret_value(), safe="")
        return f"{scheme}://{user}:{password}@{self.host}:{self.port}/{self.name}"


class SteamSettings(BaseModel):
    api_key: SecretStr = SecretStr("")


class TelegramSettings(BaseModel):
    bot_token: SecretStr = SecretStr("")
    dm_chat_id: int = 0
    group_chat_id: int = 0
    # UTC hour the daily digest fires (cron, so restarts don't drift it).
    digest_hour_utc: int = Field(default=16, ge=0, le=23)
    # Optional allowlist of Telegram chat ids that may use the bot (multi-user).
    # Comma-separated in the env var (GAMER_TELEGRAM__ALLOWED_CHAT_IDS=123,-456).
    # Empty (the default) means the bot is open to everyone; when non-empty, the
    # router's outer middleware politely refuses messages *and* callbacks from any
    # chat not listed. Parsed from a CSV env value by the before-validator below.
    allowed_chat_ids: list[int] = Field(default_factory=list)

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(item.strip()) for item in v.split(",") if item.strip()]
        return v


class TwitchSettings(BaseModel):
    client_id: SecretStr = SecretStr("")
    client_secret: SecretStr = SecretStr("")

    @property
    def enabled(self) -> bool:
        return bool(self.client_id.get_secret_value() and self.client_secret.get_secret_value())


class EmbeddingsSettings(BaseModel):
    enabled: bool = False
    model: str = "BAAI/bge-small-en-v1.5"
    # Dimensionality of the configured model; must match the pgvector column.
    dim: int = 384


class LLMSettings(BaseModel):
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


class RssSettings(BaseModel):
    """Broader-news RSS feeds (PLAN.md §3). Pluggable list of feed URLs."""

    enabled: bool = True
    # Comma-separated in the env var (GAMER_RSS__FEEDS=url1,url2); the before-
    # validator below splits the CSV.
    feeds: list[str] = Field(
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


class HealthSettings(BaseModel):
    """Self-reporting / alerting (PLAN.md §6 M4). A source silent longer than
    ``stale_after_hours`` pings the streamer once."""

    stale_after_hours: int = 24
    # Status page bind (read-only public build log).
    api_host: str = "0.0.0.0"
    api_port: int = 8080


class UISettings(BaseModel):
    """Web-UI options (UI_PLAN.md §6).

    ``public_base_url`` is the externally reachable origin of the read-only web
    UI (e.g. ``https://gamer.example.com``). When set, the daily digest appends a
    per-game deep link ``{public_base_url}/games/{id}`` to each pick so a bot
    message lands on the game page. Empty (the default) disables deep links, and
    digests are then byte-identical to before this feature.
    """

    public_base_url: str = ""


class DiscordSettings(BaseModel):
    """Discord webhook transport (M5) — proves the Transport abstraction."""

    webhook_url: SecretStr = SecretStr("")

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url.get_secret_value())


class SwitchSettings(BaseModel):
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
        # Don't JSON-decode complex (list) env values — our CSV fields
        # (rss.feeds, telegram.allowed_chat_ids) are plain comma-separated strings
        # split by their own before-validators, not JSON arrays. (Previously the
        # per-field NoDecode marker did this; it only works on BaseSettings, and
        # the nested groups are now plain BaseModel so credentials can't leak in
        # from unprefixed env vars like USER / CLIENT_ID.)
        enable_decoding=False,
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
    ui: UISettings = Field(default_factory=UISettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    switch: SwitchSettings = Field(default_factory=SwitchSettings)


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Tests can clear the cache via ``get_settings.cache_clear()``."""
    return Settings()
