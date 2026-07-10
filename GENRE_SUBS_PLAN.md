# Genre subscriptions (M7)

**Problem:** the streamer plays niche genres (e.g. Puzzle). Niche games never enter Steam's
top charts, so auto-tracking never samples them, they never become scoring candidates, and
recommendations skew mainstream. `liked_genres` exists but is only a soft fit signal.

**Feature:** subscribe to genres. A subscription is a hard commitment with three effects:

1. **Coverage** — games in a subscribed genre get `tracked=True` automatically, so the
   player-count poller samples them and they enter the candidate pool.
2. **Preference** — a new `genre_sub` score component gives candidates in subscribed
   genres a strong, explainable boost ("subscribed genre: Puzzle").
3. **Guarantee** — the digest reserves slots: at least `min(3, available)` of the daily
   picks come from subscribed genres when any qualify (a pure post-ranking quota over the
   already-scored list — no scorer hacks).

## Design

- **Prefs**: `streamer_prefs.subscribed_genres JSONB` (migration 0005, default `[]`).
  Distinct from `liked_genres` (soft taste) — subscriptions are the "always cover this" set.
- **Genre-track job** (`catalog/genre_tracking.py`, scheduled hourly): for each subscribed
  genre, mark tracked the top `N=200` games of that genre by `review_count` (via
  `game_stats`, falling back to newest release when no stats). Case-insensitive genre
  match on the JSONB array. Cap prevents tracking thousands of shovelware titles; N is a
  module constant. The job only ever sets `tracked=True` (never un-tracks — charts,
  /track, and prior subscriptions stay honored). Genres come from `steam_store`
  appdetails, which the stalest-first crawler fills over time — the job picks up newly
  detailed games on later runs.
- **Scoring**: `GenreSubComponent` (key `genre_sub`, weight 0.20 in DEFAULT_WEIGHTS):
  value 1.0 when candidate genres ∩ subscribed genres (case-insensitive), else 0.0;
  reason names the matched genre. `ScoringContext` gains `subscribed_genres`;
  `build_context` loads it from prefs.
- **Digest quota**: pure helper `apply_genre_quota(ranked, subscribed, slots=3)` — if
  fewer than `slots` of the top picks are from subscribed genres, promote the
  highest-scoring subscribed-genre candidates from below the cut, preserving relative
  order; needs candidate genres carried on `ScoredRecommendation` (add `genres` field,
  populated by the assembler from the candidate — additive, default `[]`).
- **Bot**: `/subscribe <genre>` (validates against known catalog genres,
  case-insensitive, suggests close matches on miss), `/unsubscribe <genre>`, both shown
  in `/prefs` and `/help`. Subscribing triggers an immediate genre-track pass for that
  genre so coverage starts now, not at the next hourly tick.
- **Out of scope (later):** per-genre digest sections, genre subscriptions in the web UI
  (read-only prefs display is UI-M5 territory), boosting the appdetails crawl for
  subscribed genres.

## Review gates
Existing ones, plus: the quota helper and genre matching are pure + unit-tested; the
track job is bounded (LIMIT N per genre) and EXPLAIN-checked against the genres GIN
index; digests without subscriptions are byte-identical to today.
