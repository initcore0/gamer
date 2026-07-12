# syntax=docker/dockerfile:1
# Multi-stage build. No secrets baked in — all config comes from env at runtime.

# ── Web build: compile the React SPA to static assets (web/dist). ────────────
FROM node:22-alpine AS web-build
WORKDIR /web
# Install deps from the lockfile first (cached layer) using only the manifests.
COPY web/package.json web/package-lock.json ./
RUN npm ci
# Then the source, and produce the production bundle.
COPY web/ ./
RUN npm run build

# ── Python base ──────────────────────────────────────────────────────────────
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

# Copy the built SPA and point the app at it. The catch-all serves this dist;
# /api, /status, /health stay JSON (see src/gamer/api/spa.py).
COPY --from=web-build /web/dist ./web/dist
ENV GAMER_UI__SPA_DIST=/app/web/dist

# Run as non-root.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser /app
USER appuser

ENV PATH="/app/.venv/bin:${PATH}"

# Default: apply migrations, then run the app. Overridable in compose.
CMD ["sh", "-c", "alembic upgrade head && python -m gamer"]
