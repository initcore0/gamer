# Gamer — Web UI Plan (M6)

**Goal:** grow the read-only status page into a fast, explorable web UI over everything the system has synced — browse and search the game catalog, inspect a game's signals/news/score history, watch recommendations and pipeline health. Still one box, still cheap, still safe to expose as the build-in-public window into the project.

## 1. Constraints & posture

- **Read-only and public by default.** The UI is the build-in-public artifact; it must never expose secrets or allow writes anonymously. Mutations (track a game, mute, tweak weights) come later behind an auth gate (§7) — v1 ships zero mutations, so there is nothing to secure beyond what `/status` already shows.
- **Fast on cheap hardware.** Budget: <100ms server render for any page over a multi-million-row `signals_samples` table. That is an indexing-and-query problem, not a framework problem — §5 is the real heart of this plan.
- **No heavy frontend toolchain.** One Python service, no Node build step to babysit on the box or in CI.

## 2. Stack decision: server-rendered FastAPI + Jinja2 + HTMX

Extend the existing `api/` module rather than adding a second service.

| Piece | Choice | Why |
|---|---|---|
| Server | Existing FastAPI app in `src/gamer/api/` | Already wired into the app lifecycle, uvicorn, healthcheck |
| Templating | **Jinja2** (replace the f-string HTML) | Real templates, autoescaping by default (we already hand-escape; templates make it structural) |
| Interactivity | **HTMX** (self-hosted single ~14kB file) | Live search-as-you-type, paginated "load more", auto-refreshing health — all as HTML fragments, no SPA, no JSON client state |
| Sprinkle JS | **Alpine.js** only if a component truly needs it | Keep near-zero custom JS |
| CSS | Hand-rolled single stylesheet (extend the current one), CSS variables, dark-mode via `prefers-color-scheme` | No Tailwind build step |
| Charts | **uPlot** (self-hosted, ~40kB) for time-series; inline SVG sparklines rendered server-side for list rows | uPlot is the fastest OSS TS chart lib; sparklines cost nothing client-side |
| Static assets | Self-hosted under `/static`, served by FastAPI `StaticFiles`, far-future cache headers + content hash in filename | No CDN (works offline, no third-party calls on a public page) |

Why not a SPA (React/Svelte): the data is server-side, the pages are read-only lists and detail views, and every SPA adds a Node toolchain, an API-serialization layer, and client state for no user-visible gain here. HTMX gives the "feels instant" interactions (search, infinite scroll, live refresh) with server-rendered fragments. If a future feature genuinely needs richness (e.g. an interactive score-tuning workbench), it can be an island on one page.

**JSON API stays.** Every page handler is a thin wrapper over a query function that also backs a `/api/v1/...` JSON twin (games list/detail, search, status). Cheap to do since the data layer is shared, and it keeps the door open for other clients.

## 3. Information architecture (pages)

```
/                      Dashboard (evolved status page)
/games                 Catalog browser: search + filters + sort, paginated
/games/{id}            Game detail: signals charts, news, score history
/recommendations       Recommendation feed with expandable "why" breakdowns
/news                  News stream (clustered), filter by game/source
/sources               Pipeline health (per-source runs, cursors, job errors)
/api/v1/...            JSON twins of the above
```

1. **Dashboard `/`** — what the status page shows today, redesigned: headline counts, top movers sparkline strip, latest recommendations, stale-source warnings, last digest sent. Every element links into the deeper pages.
2. **Catalog `/games`** — the centerpiece.
   - **Search box** (HTMX `keyup changed delay:250ms` → fragment swap): fuzzy name search (§5).
   - **Filters:** platform, genre (chips), free/paid, tracked-only, "has recent signals".
   - **Sort:** current players, 24h delta, review count, release date, recently updated, name.
   - **Rows:** name, platform badge, genres, current players + 7-day inline SVG sparkline, review count, tracked flag. Keyset-paginated "load more" (§5).
3. **Game detail `/games/{id}`** — everything we know about one game:
   - Header: name, platform, genres, price, release date, store link, tracked status.
   - **Charts (uPlot):** player count (range picker: 24h / 7d / 30d / all — served from rollups beyond 7d), review-count growth, Twitch viewers overlay when present.
   - **Score panel:** latest recommendation's full component/penalty breakdown rendered as labeled bars (the `/why` data, visual).
   - **News tab:** this game's news items, cluster-deduped (one card per cluster, "+2 similar" expander).
   - **Similar games:** pgvector nearest neighbors on the game embedding — cheap win, great for exploration.
4. **Recommendations `/recommendations`** — chronological feed of `recommendations` rows grouped by run; each row expands (HTMX) to the full breakdown; sent/unsent and feedback verdicts shown when present.
5. **News `/news`** — cluster-grouped stream across all sources, filterable by source/game/date; links into game pages when `game_id` is set.
6. **Sources `/sources`** — the ops view: per-source last run/success, cursor summary, recent `jobs` rows with durations and errors (errors are already secret-redacted at write time), event counts per day (small bar chart).

## 4. Module layout

```
src/gamer/api/
  app.py            FastAPI wiring only (routes → handlers, static, templates)
  deps.py           shared query-param parsing (pagination cursor, filters)
  queries/          the data layer — plain async functions returning dataclasses
    games.py        list_games(filters, sort, cursor), game_detail(id), similar()
    signals.py      series(game_id, metric, range) — rollup-aware
    recs.py         recent_runs(), breakdown(id)
    news.py         clustered_stream(filters)
    status.py       (move build_status here)
  routes/           one module per section; each route = query + template render
  templates/        Jinja2; base.html + per-page + _fragments/ for HTMX partials
  static/           css, htmx.min.js, uplot, favicon — all vendored
tests/api/          fragment + JSON tests via httpx.ASGITransport (no server)
```

Rules for the implementing agents:
- **Queries never live in route handlers.** Every route calls one `queries/` function; that same function backs the JSON twin. Query functions are the unit-test surface (integration-marked against Postgres, with pure shaping helpers unit-tested DB-free).
- **HTMX endpoints return fragments** (`templates/_fragments/`), full pages extend `base.html`. A fragment route is the same handler with an `HX-Request` check.
- All user-supplied values rendered via template autoescape; all query params validated through `deps.py` (sort keys and filter values are allowlisted enums, never interpolated into SQL).

## 5. Performance design (the actual work)

The catalog is ~100k+ games and `signals_samples` grows ~50k rows/day. Fast pages come from these five decisions:

1. **New indexes (one alembic migration):**
   - `CREATE EXTENSION pg_trgm` + GIN trgm index on `lower(games.name)` → fuzzy `ILIKE '%q%'` search in ms.
   - Partial index on `games (tracked) WHERE tracked` and composite `(platform, release_date desc)` for common filters/sorts.
   - `news_items (published_at desc)` and `(cluster_id, published_at)` for the news stream.
   - HNSW index on the pgvector embedding columns (games/news) for similar-games and cluster queries.
2. **Search =** trgm similarity on name, ranked by `similarity() DESC, current_players DESC`. One query, no search engine. (Semantic "vibe" search via pgvector is a stretch goal on the same page — a toggle, not a replacement.)
3. **Keyset pagination everywhere.** Cursors are `(sort_value, id)` tuples encoded in an opaque token; never `OFFSET` (page 400 of games must cost the same as page 1).
4. **Precomputed list-row stats.** The catalog page must not aggregate signals per row at request time. Add a tiny `game_stats` table (or materialized view) — `game_id, current_players, players_24h_delta, players_7d_spark (float[] of 21 points), review_count, last_signal_at` — refreshed by a scheduled job every 15 min (`REFRESH ... CONCURRENTLY` or an upsert job over tracked games). List pages join it; sparklines render from the stored array with zero extra queries.
5. **Chart data from rollups.** ≤7d ranges read raw samples; longer ranges read `signals_rollups` (the table exists; M6 adds the rollup-writer job if M4 didn't). Chart endpoints return compact JSON arrays (`[[ts...],[v...]]`) with `Cache-Control: max-age=300`, since sample granularity is hourly anyway.

Plus the boring wins: gzip middleware, ETag on fragments, template caching, a `pool_size` sanity check, and a p95 log line per route so slow pages are visible on `/sources`.

## 6. Design language

Consistent with the existing status page but intentional: system-ui type, one accent color, generous whitespace, tabular numerals for all counts, platform badges, dark mode via CSS variables. No component library — the UI is tables, cards, charts, and a search box; hand-rolled CSS keeps it under ~10kB. Mobile: single-column collapse; the dashboard and game detail must be readable on a phone (that's where the Telegram digest links will land — every digest/`/why` message gains a deep link into the UI, closing the loop between bot and web).

## 7. Auth & exposure (deferred, decided now)

- v1 ships read-only; bind stays `0.0.0.0:8080` behind whatever the box already does (recommend putting Caddy in front for TLS + gzip when it gets a domain).
- When mutations arrive (track/untrack from the catalog page, mute from the game page, weight tuning), gate them behind a single shared-secret session (`GAMER_UI__ADMIN_TOKEN`, SecretStr, login form sets a signed cookie). No user accounts — one streamer.
- Never render: config, env, tokens, cursor internals beyond source names, raw job error strings unfiltered (they are redacted at write time, but the sources page truncates them anyway).

## 8. Milestones

**UI-M1 — Foundation (unblocks everything)**
Jinja2 + StaticFiles + base template/CSS, vendored HTMX/uPlot, `queries/` layer with the status page ported to it, migration with trgm + new indexes, keyset-pagination helper + tests. *Exit: old status page pixel-equivalent on the new stack; `/games` renders a plain paginated list.*

**UI-M2 — Catalog & search (the headline feature)**
`game_stats` job + table, `/games` with search/filters/sort/sparklines/load-more, JSON twin. *Exit: fuzzy search over the full catalog returns in <100ms server-side; list pages never touch `signals_samples` directly.*

**UI-M3 — Game detail & charts**
`/games/{id}` with uPlot player/review/Twitch charts (rollup-aware ranges), score-breakdown panel, news tab, similar-games via pgvector. Rollup-writer job if missing. *Exit: any tracked game tells its full story on one page.*

**UI-M4 — Recommendations, news, sources**
The three remaining sections + dashboard redesign + digest deep links. *Exit: every entity the pipeline produces is reachable and explorable from `/`.*

**UI-M5 (stretch)** — semantic search toggle, weight-tuning workbench (first authed mutation), SSE live-updating dashboard.

## 9. Delegation & review gates

Same discipline as M0–M5: UI-M1 lands as one PR (foundation is coupled); after that, one agent per section — the `queries/` function signatures and the fragment/page convention are the contract, so section agents don't collide. Review gates per PR: no SQL in route handlers, allowlisted sort/filter params, autoescape untouched, no external asset URLs (CSP will enforce: `default-src 'self'`), integration tests for each query function, and an `EXPLAIN`-verified index hit for every new list query (paste the plan in the PR description).
