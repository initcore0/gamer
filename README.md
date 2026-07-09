# gamer

A self-hosted service that watches the Steam ecosystem — game news, player counts,
trends, releases — **scores what a streamer should play next**, and pushes
recommendations to Telegram (bot DM + group broadcast).

Built **in public**: there are no secrets in this repo, ever. See [SECURITY.md](SECURITY.md).

> Full design in [PLAN.md](PLAN.md).

## Status

Milestone **M0 — skeleton & safety rails** is in place:

- `uv` + `pyproject.toml` packaging, `ruff` + `mypy` + `pytest`
- Pydantic-Settings config (env-only, `SecretStr` for every credential)
- SQLAlchemy 2.0 async models + alembic migrations (Postgres 16 + pgvector)
- Core contracts: `Source` (ingestion) and `Transport` (notify) protocols
- Polite HTTP client (rate limit + retry/backoff)
- Docker Compose (app + postgres, optional ollama), CI with lint/type/test + **gitleaks (blocking)**
- pre-commit hooks (gitleaks, ruff, hygiene)

Next: **M1** (Steam ingestion) and **M2** (Telegram notify) — see PLAN.md §6.

## Quick start

```bash
make install          # deps + pre-commit hooks
cp .env.example .env   # fill in real values (see SECURITY.md)
make up                # docker compose: postgres + app (runs migrations)
```

Local dev without Docker (needs a reachable Postgres with the `vector` extension):

```bash
make migrate          # alembic upgrade head
make run              # python -m gamer
make check            # lint + type + test — what CI enforces
```

## Layout

```
src/gamer/
  config.py     logging.py   app.py   scheduler.py   __main__.py
  db/           models (games, signals, news, recommendations, outbox, …) + engine
  sources/      Source protocol, polite HTTP client, per-source adapters (M1)
  notify/       Transport protocol (Telegram first)                        (M2)
  catalog/  signals/  enrichment/  scoring/  bot/                          (M1–M4)
migrations/     alembic
tests/          unit tests — no live API calls (fixtures only)
```

## Contributing (agents & humans)

- Every PR must pass `gitleaks`, lint, type-check, and tests.
- **No live API calls in unit tests** — use `respx`/fixtures.
- New upstream? Implement `gamer.sources.base.Source` and register it. The
  protocol is the contract; a broken source degrades, never crashes the app.
- New delivery channel? Implement `gamer.notify.base.Transport`.
