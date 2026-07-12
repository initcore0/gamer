# Multi-user support

**Problem:** the system was single-user. `bot/handlers.py` hardcoded
`_PREF_KEY = "default"`, so every Telegram user mutated the *same*
`streamer_prefs` row; `recommendations` rows were global (no owner); and the
daily digest went to one configured group. Multiple people must be able to use
the bot with individual preferences, recommendations, feedback-driven taste, and
their own DM digest.

## Key scheme

- `pref_key = str(telegram_chat_id)`. In Telegram, **DM** chat ids are positive
  (equal to the user id); **group/supergroup** ids are negative.
- `"default"` remains the legacy/global profile that predates multi-user.
- Derivation is a pure helper (`bot/keys.py::pref_key_from_event`) over an
  aiogram `Message` / `CallbackQuery` (`str(event.chat.id)` / the callback's
  message chat), unit-tested directly.
- Schema (migration **0006**, head was 0005):
  - `streamer_prefs.label TEXT NULL` — a human display name (DM: the user's full
    name, group: the chat title). Legacy `'default'` keeps a NULL label.
  - `recommendations.pref_key VARCHAR(64) NOT NULL DEFAULT 'default'` — the owning
    profile. Existing rows backfill to `'default'` via the server default.
  - index `ix_rec_prefkey_created` on `(pref_key, created_at DESC)` — backs the
    per-profile cooldown lookup and the `user_key`-filtered feed.

## Per-profile scoring

- `build_context(session, now, key)` filters `last_recommended` by
  `Recommendation.pref_key == key`, so a game one user just got recommended is
  **not** on cooldown for anyone else.
- `recommend(key=…)` threads the key through; `_persist` stamps `pref_key=key`
  on every row it writes.
- The feedback→taste loop is scoped per profile: `compute_profile_embedding` /
  `update_profile_embedding` (in `scoring/components/fit.py`) join
  `Feedback → Recommendation.pref_key → StreamerPref`, so each user's taste
  vector is learned only from their own 👍/▶️.

## Legacy adoption

When a prefs row is first created for a chat id that equals the operator's
configured `dm_chat_id` **or** `group_chat_id`, it is seeded by copying the
legacy `'default'` row's fields (liked/blocked/subscribed genres, muted ids,
`digest_enabled`, `profile_embedding`) if that row exists. Everyone else starts
blank. The `'default'` row is **never** deleted or modified. This preserves the
current owner's existing puzzle-genre subscriptions across the migration.

## Bot commands

`_PREF_KEY` is gone. Every handler resolves the caller's key via the pure helper
and operates on *that* profile:

- `/recommend` → `recommend(limit=5, key=<caller>)`.
- `/why` prefers the caller's latest rec for the game, falling back to any
  profile's rec (so `/why` still explains a group-digest pick).
- `/mute`, `/subscribe`, `/unsubscribe`, `/prefs`, `/digest on|off`, and the
  `/genres` inline panel + callbacks all read/write the caller's profile — the
  checkmarks in the `/genres` panel reflect the caller's subscriptions.
- Feedback callbacks (`feedback:<verdict>:<rec_id>`) stay keyed by rec id — the
  rec now knows its owner via `pref_key`.

## Digest fan-out

`run_digest_once` now produces two fan-outs, enqueued then dispatched in one
outbox batch:

- **Group** broadcast — scored for the group's own profile
  (`str(group_chat_id)`), falling back to `'default'` when no group prefs row
  exists yet (so subscriptions keep applying right after the migration, before
  anyone talks to the bot). Mirrored to Discord when configured. `_digest_enabled`
  checks the group key, falling back to `'default'` (missing row → enabled).
- **Per-user DM** — for every prefs row with `digest_enabled=True` whose key is a
  DM chat (positive int, excluding the group id), `recommend(limit=10, key,
  subscribed_quota=3)` is delivered to that chat. Empty recs → skip silently (no
  spam). A failure for one user is logged and never aborts the others. Selection
  is a pure helper (`digest.py::select_dm_digest_keys`), unit-tested.
- Delivery: `Notification` gained an optional `target_chat_id`; the Telegram
  transport sends there when set, else its configured default chat. This lets one
  DM transport fan out to every subscriber. The DM dedup key includes the target
  chat (`digest:<date>:dm:<chat_id>`) so each user's digest dedups
  independently. The outbox `enqueue → dispatch_pending` contract is unchanged.

## Allowlist (optional)

- New setting `GAMER_TELEGRAM__ALLOWED_CHAT_IDS` (CSV of ints, parsed like
  `RssSettings.feeds`). **Empty = open to everyone** (the default posture).
- When non-empty, an aiogram **outer** middleware on the router
  (`bot/middleware.py`) politely refuses messages **and** callbacks from any chat
  not on the list (answering so the client isn't left spinning). The decision is
  a pure function (`bot/keys.py::is_chat_allowed`), unit-tested.

## API query layer (minimal)

`api/queries/recs.py`: `RecRow` gains a `user_key` field (the rec's `pref_key`),
and `list_recommendations` accepts an optional `user_key: str | None` filter
(`None` = all profiles). The existing JSON route echoes `user_key`. The full
`/api/v1/recommendations?user_key=…` contract (including `"all"` semantics and
the refresh route) lands in the follow-up API PR.
