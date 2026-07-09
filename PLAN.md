# Gamer — Architecture & Delivery Plan

**What it is:** A self-hosted service that watches the Steam ecosystem (game news, player counts, trends, releases), scores what a streamer should play next, and pushes recommendations to Telegram (bot DM + group broadcast). Built in public — zero secrets in the repo, ever.

---

## 1. Guiding constraints

- **Build in public** → secret hygiene is a first-class requirement, not an afterthought.
- **Cheap / local-first** → self-hosted on one Linux box (good GPU/CPU/RAM). Local models (embeddings, small LLM) over paid APIs. External calls only to free-tier APIs.
- **Steam first**, but the source layer must be pluggable (Xbox/PS/Switch later).
- **Telegram first**, but notifications go through a transport abstraction (Discord, email, webhooks later).

## 2. Tech stack (decisions)

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12, asyncio | Best glue for scraping, ML/embeddings, Telegram libs; team of agents can move fast |
| Database | **PostgreSQL 16 + pgvector** | One box, always-on; concurrent workers write safely (SQLite would contend); pgvector gives free semantic search; TimescaleDB optional later for player-count time series |
| Job orchestration | APScheduler (in-process) + a `jobs` table for state | No Redis/Celery needed at this scale; cron-like schedules, checkpointed in DB |
| Embeddings | Local via `sentence-transformers` (e.g. `bge-small-en-v1.5`) on the GPU | Free, fast, good enough for news dedup + topic clustering |
| Optional LLM | Ollama (e.g. Llama 3.1 8B / Qwen) for summarizing news into the notification blurb | Local, free; feature-flagged so the system works without it |
| Telegram | `aiogram` v3 | Modern asyncio bot framework |
| HTTP | `httpx` + `tenacity` (retry/backoff), per-source rate limiters | Politeness toward free APIs |
| Packaging | `uv` + `pyproject.toml`, Docker Compose (app + postgres + ollama) | Reproducible on the Linux box |
| Config | Pydantic Settings, env-vars only | No secrets in files |

## 3. Data sources (Steam-focused)

**Important:** SteamDB has **no public API and its ToS forbids scraping**. Since we're building in public, we don't scrape it. Everything SteamDB shows is derivable from official/free sources:

| Data | Source | Auth | Notes |
|---|---|---|---|
| App catalog (all appids) | `ISteamApps/GetAppList`, `IStoreService/GetAppList` | Steam API key | Nightly sync |
| Game details (genres, tags, price, release date) | Store API `appdetails` | none | Heavily rate-limited (~200 req/5min) — crawl slowly, cache hard |
| Current/peak players | `ISteamUserStats/GetNumberOfCurrentPlayers`, `ISteamChartsService/GetMostPlayedGames` | key/none | The core popularity signal; poll top-N hourly |
| Top sellers / trending | Store search & charts endpoints; SteamSpy (free) | none | SteamSpy for ownership estimates |
| Game news / patch notes | `ISteamNews/GetNewsForApp` | none | Per-app news feed — updates, events, DLC |
| Reviews velocity | Store `appreviews` endpoint | none | Review count deltas = hype signal |
| Broader news | RSS (PC Gamer, RPS, Eurogamer…), r/games etc. via public JSON | none | Pluggable `NewsSource` interface |
| Streaming meta | Twitch Helix API (free tier) | client id/secret | "What's being watched" — strong signal for what to stream; optional in v1 |

All API keys are free-tier. Each source implements a common `Source` interface: `fetch() -> list[RawEvent]`, with per-source rate limits, ETag/If-Modified-Since caching, and checkpoint cursors in the DB.

## 4. System architecture

Single deployable app (modular monolith) with clear internal boundaries — split later only if needed.

```
                    ┌────────────────────────────────────────────┐
                    │                  gamer app                  │
  Steam Web API ──▶ │ ┌──────────┐  ┌───────────┐  ┌───────────┐ │
  Steam Store   ──▶ │ │ Ingestion │─▶│ Enrichment│─▶│  Scoring  │ │
  SteamSpy      ──▶ │ │ (sources) │  │(embed,LLM)│  │  engine   │ │
  RSS / Reddit  ──▶ │ └──────────┘  └───────────┘  └─────┬─────┘ │
  Twitch        ──▶ │        ▲            ▲              ▼       │
                    │   APScheduler   Ollama/GPU   ┌───────────┐ │
                    │                               │ Notifier  │ │
                    │        Postgres + pgvector    │(transports)│ │
                    └───────────────────────────────┴─────┬─────┘
                                                          ▼
                                             Telegram bot + group
                                             (Discord/webhook later)
```

### Modules

1. **`sources/`** — one adapter per upstream (steam_api, steam_store, steamspy, rss, twitch). Emit normalized `RawEvent`s (news item, player-count sample, price change, release). Idempotent via natural keys.
2. **`catalog/`** — game registry. `Game` is platform-agnostic (`platform` enum: steam now; xbox/psn/switch later), with `platform_app_id`, tags, genres.
3. **`signals/`** — time-series store of metrics per game: player counts, review velocity, news frequency, Twitch viewers. Append-only samples + rollups.
4. **`enrichment/`** — embeds news items (pgvector), dedups near-identical stories, clusters topics, optional local-LLM summary. Runs on the GPU box.
5. **`scoring/`** — the recommender. v1 is a **transparent weighted score**, not ML:
   - `momentum` — player-count growth rate (7d slope, z-score vs the game's own baseline)
   - `hype` — news/review/announcement velocity (release, big patch, DLC drop)
   - `watchability` — Twitch viewers-to-players ratio (streams well vs. plays well)
   - `freshness` — recency of release or major update
   - `fit` — cosine similarity to the streamer's profile (games they liked, genre prefs) via embeddings
   - Penalties: already streamed recently, on cooldown, blocklisted genres.
   Every recommendation stores its **score breakdown** (explainability: "why this game") — shown in the notification.
6. **`notify/`** — transport abstraction:
   ```python
   class Transport(Protocol):
       async def send(self, msg: Notification) -> DeliveryResult
   ```
   v1: `TelegramDM` (streamer bot chat, interactive: 👍/👎/"played it" buttons feed back into `fit`) and `TelegramGroup` (broadcast, read-only digest). Outbox table + delivery log so sends are retried and never duplicated.
7. **`bot/`** — aiogram command surface: `/recommend`, `/why <game>`, `/mute <game>`, `/prefs`, `/digest on|off`.
8. **`api/` (optional, M4)** — small FastAPI read-only endpoint / status page for the public build log.

### Data model (core tables)

`games`, `game_tags`, `signals_samples(game_id, metric, ts, value)`, `signals_rollups`, `news_items(embedding vector, cluster_id)`, `recommendations(score, breakdown jsonb, sent_at)`, `feedback(rec_id, verdict)`, `streamer_prefs`, `outbox`, `source_cursors`, `jobs`.

## 5. Secret hygiene (build-in-public)

- **No secrets in the repo, ever.** Config only via env vars; `.env` is gitignored; `.env.example` documents every var with fake values.
- **gitleaks** as a pre-commit hook *and* a CI job (fails the build on any leak, full-history scan on the hook's first install).
- GitHub push protection + secret scanning enabled on the repo.
- Secrets needed: Steam API key, Telegram bot token, Twitch client id/secret, Postgres password (local only). All free-tier; all rotatable — document rotation steps in `SECURITY.md`.
- Logs must never print config objects; Pydantic `SecretStr` for every credential.
- Docker Compose reads from `.env`; no secrets in compose file or Dockerfiles.
- CI (GitHub Actions) uses repo secrets only for deploy; never echoed.

## 6. Milestones

**M0 — Skeleton & safety rails (small)**
Repo scaffold (`uv`, ruff, mypy, pytest), Docker Compose (app+postgres), CI with lint/test/gitleaks, pre-commit hooks, `.env.example`, `SECURITY.md`, config module, DB migrations (alembic). *Exit: CI green, gitleaks blocking, `docker compose up` boots an empty app.*

**M1 — Steam ingestion & catalog**
`sources/` framework (rate limiting, cursors, idempotency), app-list sync, appdetails crawler, player-count poller (top ~2k games hourly + tracked games), Steam news poller, SteamSpy. Signals tables + rollups. *Exit: DB fills continuously for 48h without manual intervention; metrics queryable.*

**M2 — Telegram notify (walking skeleton end-to-end)**
Transport abstraction, outbox, aiogram bot, group broadcast. A naive "top movers today" digest wired from real M1 data — no scoring engine yet. *Exit: daily digest lands in the Telegram group; `/recommend` answers with top movers.*

**M3 — Scoring engine & personalization**
Weighted scorer with breakdowns, streamer prefs, feedback buttons feeding `fit`, cooldowns/blocklist, embeddings for news dedup + game-similarity (`pgvector`), backtest harness (replay last N weeks of signals, eyeball the picks). *Exit: recommendations are explainable (`/why`), feedback loop works.*

**M4 — Enrichment & polish**
Ollama summaries for the digest (feature-flagged), Twitch watchability signal, RSS news sources, clustering of duplicate news, status page, alerting on stale sources (a source silent >24h pings the streamer). *Exit: digest reads like a human wrote it; system self-reports health.*

**M5 — Extensibility proofs (later)**
Second transport (Discord webhook) to prove `Transport`; stub a second platform (e.g. Switch eShop releases via free feed) to prove the platform abstraction.

## 7. Risks & mitigations

- **Steam store rate limits** (appdetails is stingy) → slow crawler with priority queue (popular games first), aggressive caching, respect 429s.
- **SteamDB temptation** → explicitly out of scope; everything needed comes from official endpoints (documented above).
- **Unofficial endpoints change** (store search/charts) → adapter isolation + contract tests per source; a broken source degrades, doesn't crash.
- **Scoring quality is subjective** → transparent breakdowns + feedback loop + backtest harness instead of chasing ML upfront.
- **One-box ops** → Docker Compose, nightly `pg_dump` to a second disk, healthcheck endpoint, systemd unit for restart-on-boot.

## 8. Delegation plan (for agents)

Each milestone splits into agent-sized tasks with crisp interfaces: M0 (scaffold / CI+gitleaks / compose), M1 (source framework, then one agent per source adapter — the `Source` protocol is the contract), M2 (transport+outbox / bot commands), M3 (scorer / feedback / backtest). Review gates: every PR must pass gitleaks, contract tests for touched sources, and include no live-API calls in unit tests (fixtures only).
