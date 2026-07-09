# gamer

A self-hosted service that watches the Steam ecosystem — game news, player counts,
trends, releases — **scores what a streamer should play next**, and pushes
recommendations to Telegram (bot DM + group broadcast).

Built **in public**: there are no secrets in this repo, ever. See [SECURITY.md](SECURITY.md).

> Full design in [PLAN.md](PLAN.md).

## Status

**M0 (skeleton & safety rails)** ✅ · **M1 (Steam ingestion)** and **M2 (Telegram
walking skeleton)** ✅ core paths landed.

- **M0** — `uv`/`ruff`/`mypy`(strict)/`pytest`; env-only config with `SecretStr`;
  SQLAlchemy 2.0 async + alembic (Postgres 16 + pgvector); `Source`/`Transport`
  contracts; polite HTTP client; Docker Compose; CI with lint/type/test +
  **blocking full-history gitleaks**; pre-commit hooks.
- **M1** — `steam_api` (catalog + player counts) and `steam_store` (appdetails,
  news, reviews) source adapters; the ingestion runner + idempotent `DbEventSink`
  persisting into games/signals/news; cursors, rate limits, graceful degradation.
  *Verified end-to-end against live Postgres + the real Steam player-count API.*
- **M2** — Telegram DM/group transports (aiogram v3) + a reliable dedup/retry
  **outbox**; `top movers` digest; bot commands (`/recommend`, `/why`, `/mute`,
  `/prefs`, `/digest`) and the 👍/👎/played feedback loop. Wired into the
  scheduler in `jobs.py`.

Known follow-up: the `ISteamApps/GetAppList` catalog endpoint is currently 404ing
from some networks (player-count ingestion is unaffected) — tracked separately.

Next: **M3** (scoring engine & personalization) — see PLAN.md §6.

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
