# syntax=docker/dockerfile:1
# Multi-stage build. No secrets baked in — all config comes from env at runtime.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:0.5.21 /uv /uvx /bin/

WORKDIR /app

# Install deps first (cached layer) using only the manifests.
COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# Then the source.
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --no-dev || true

# Run as non-root.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

ENV PATH="/app/.venv/bin:${PATH}"

# Default: apply migrations, then run the app. Overridable in compose.
CMD ["sh", "-c", "alembic upgrade head && python -m gamer"]
