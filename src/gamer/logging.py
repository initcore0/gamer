"""Structured logging setup.

Rule (PLAN.md §5): logs must never print config objects or credentials. We use
structlog; ``SecretStr`` already masks itself, but never log a whole ``Settings``.
"""

from __future__ import annotations

import logging
import re
import sys

import structlog

# Credential-bearing query parameters upstream URLs may carry (Steam's ``key``,
# OAuth-style tokens…). httpx exception strings embed the full request URL, so
# anything derived from ``str(exc)`` must pass through redact_secrets before
# being logged or persisted.
_SECRET_PARAM_RE = re.compile(
    r"\b(key|api_?key|token|access_token|client_secret)=[^&\s'\"]+",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Mask credential query-parameter values in free text (URLs, error strings)."""
    return _SECRET_PARAM_RE.sub(r"\1=***", text)


def configure_logging(*, level: str = "INFO", json: bool = False) -> None:
    """Configure structlog + stdlib logging once at startup."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    # httpx/httpcore log every request URL at INFO — including credential query
    # params (Steam's key=…) that redact_secrets never sees because these lines
    # bypass structlog. Keep them at WARNING so no URL ever hits the logs.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
