# gamer API v1 contract

The JSON surface the React UI (and anything else) builds against. This document
is the source of truth for request/response shapes: the backend implements it,
the frontend consumes it, and neither side changes it without updating this file
in the same PR.

Conventions:

- Base path `/api/v1`. All bodies JSON. Timestamps are ISO-8601 UTC strings.
- Errors: standard FastAPI shape `{"detail": ...}` with 404/422 status codes.
- List endpoints use opaque keyset cursors: pass back `next_cursor` verbatim as
  `cursor`; `null` means last page. Cursors never 500 — garbage degrades to the
  first page.
- Empty-string query params mean "not provided" (same as omitting them).
- `user_key` identifies a preference profile: `"default"` is the legacy/global
  profile; bot users get `str(telegram_chat_id)`.

## Catalog

### GET /api/v1/games
Query: `q` (substring, ILIKE-escaped), `platform` (`steam|xbox|psn|switch`),
`genre`, `tracked` (bool), `active` (bool), `sort`
(`name|players|delta|reviews`), `cursor`, `limit` (1–200, default 50).

```json
{
  "games": [
    {
      "id": 1, "name": "Celeste", "platform": "steam", "genres": ["Platformer"],
      "tracked": true, "current_players": 1234.0, "players_24h_delta": 56.0,
      "spark": [1.0, 2.0], "review_count": 9000.0,
      "last_signal_at": "2026-07-12T14:00:00+00:00"
    }
  ],
  "next_cursor": "b64token"
}
```

### GET /api/v1/games/{id}
404 if unknown. Game header + latest score breakdown + related content in one
call (the game page needs no other request except series):

```json
{
  "id": 1, "name": "...", "platform": "steam", "platform_app_id": 504230,
  "genres": [], "release_date": null, "price_cents": 1999, "is_free": false,
  "tracked": true, "current_players": 0.0, "players_24h_delta": 0.0,
  "review_count": 0.0, "twitch_viewers": null, "last_signal_at": null,
  "steam_url": "https://store.steampowered.com/app/504230",
  "breakdown": {"score": 0.42, "breakdown": {"momentum": {"weighted": 0.2, "reason": "..."}}, "created_at": "..."},
  "news": [{"id": 1, "title": "...", "url": "...", "source": "...", "published_at": "..."}],
  "similar": [{"id": 2, "name": "...", "genres": [], "current_players": 0.0}]
}
```

### GET /api/v1/games/{id}/series?metric=players&range=7d
`metric`: `players|review_count|news_count|twitch_viewers|price_cents`;
`range`: `7d|30d|90d` (default `7d`). → `{"ts": [epoch_s], "values": [float]}`
with `Cache-Control: public, max-age=300`.

### GET /api/v1/genres
→ `{"genres": ["Action", "Puzzle", ...]}` (canonical casing, sorted).

## Recommendations

### GET /api/v1/recommendations
Query: `user_key` (default `"default"`; `"all"` returns every profile's rows),
`cursor`, `limit` (1–100, default 20).

```json
{
  "recommendations": [
    {
      "id": 9, "game_id": 1, "game_name": "Celeste", "score": 0.61,
      "user_key": "default",
      "created_at": "...", "sent_at": null,
      "feedback": {"up": 0, "down": 0, "played": 0},
      "breakdown": {"momentum": {"weighted": 0.2, "reason": "..."}}
    }
  ],
  "next_cursor": null
}
```

### POST /api/v1/recommendations/refresh
Body: `{"user_key": "default", "limit": 10}` (limit 1–20, default 10). Runs the
scorer for that profile now (persisting, `subscribed_quota=3`) and returns the
fresh picks in the same row shape as the GET (feedback counts all zero,
`next_cursor` absent): `{"recommendations": [...]}`. 422 for an unknown
`user_key`.

## Users / profiles

### GET /api/v1/users
→ profiles for the user-switcher:

```json
{
  "users": [
    {
      "key": "default", "label": "Legacy profile",
      "liked_genres": [], "blocked_genres": [], "subscribed_genres": ["Puzzle"],
      "muted_count": 1, "digest_enabled": true,
      "created_at": "..."
    }
  ]
}
```

## News

### GET /api/v1/news
Query: `source`, `game_id`, `cursor`, `limit` (1–100, default 30). Unknown
`source` degrades to unfiltered.

```json
{
  "news": [
    {"id": 1, "title": "...", "url": "...", "source": "pcgamer",
     "published_at": "...", "game_id": null, "game_name": null,
     "cluster_id": 5, "similar_count": 1,
     "similar": [{"id": 2, "title": "...", "source": "...", "url": "..."}]}
  ],
  "next_cursor": null
}
```

### GET /api/v1/news/sources
→ `{"sources": ["pcgamer", "rps", ...]}` (the filter allowlist).

## Ops

### GET /api/v1/status
Unchanged legacy payload (uptime, per-source freshness, stale_sources, counts).

### GET /api/v1/dashboard
Everything the dashboard page renders beyond `/status`:

```json
{
  "top_movers": [{"game_id": 1, "name": "...", "latest": 1000.0, "delta": 50.0, "pct": 5.3}],
  "latest_recommendations": [{"id": 9, "game_id": 1, "game_name": "...", "score": 0.61, "user_key": "default", "created_at": "..."}],
  "last_digest": {"channel": "telegram_group", "sent_at": "..."},
  "next_digest_at": "2026-07-13T16:00:00+00:00"
}
```

### GET /api/v1/sources
Unchanged: `{"sources": [{source, last_run_at, last_success_at, stale, jobs: [...]}], "events_per_day": [{day, samples, news, games}]}`.

## CORS & serving

- The React build is served by the app at `/` (SPA fallback: unknown non-`/api`,
  non-`/static` GET paths return `index.html`).
- CORS: `GAMER_UI__CORS_ORIGINS` (CSV, default empty = same-origin only). Vite
  dev runs against `http://localhost:8080` via dev-server proxy, so CORS stays
  off in prod.
- No auth in v1 (LAN deployment).
