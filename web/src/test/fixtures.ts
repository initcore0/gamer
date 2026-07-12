// Fixtures matching the documented API_CONTRACT.md shapes exactly. Shared by the
// MSW handlers and by assertions in tests.

import type {
  DashboardPayload,
  GameDetail,
  GameListItem,
  NewsItem,
  Recommendation,
  SourcesPayload,
  StatusPayload,
  UserProfile,
} from "../api/types";

export const gamesPage1: GameListItem[] = [
  {
    id: 1,
    name: "Celeste",
    platform: "steam",
    genres: ["Platformer"],
    tracked: true,
    current_players: 1234,
    players_24h_delta: 56,
    spark: [1000, 1100, 1180, 1234],
    review_count: 9000,
    last_signal_at: "2026-07-12T14:00:00+00:00",
  },
  {
    id: 2,
    name: "Hades",
    platform: "steam",
    genres: ["Roguelike", "Action"],
    tracked: false,
    current_players: 8800,
    players_24h_delta: -120,
    spark: [9000, 8950, 8800],
    review_count: 250000,
    last_signal_at: "2026-07-12T13:00:00+00:00",
  },
];

export const gamesPage2: GameListItem[] = [
  {
    id: 3,
    name: "Dota 2",
    platform: "steam",
    genres: ["MOBA"],
    tracked: true,
    current_players: 500000,
    players_24h_delta: 4200,
    spark: [480000, 495000, 500000],
    review_count: 2000000,
    last_signal_at: "2026-07-12T14:30:00+00:00",
  },
];

export const gameDetailFixture: GameDetail = {
  id: 1,
  name: "Celeste",
  platform: "steam",
  platform_app_id: 504230,
  genres: ["Platformer"],
  release_date: "2018-01-25T00:00:00+00:00",
  price_cents: 1999,
  is_free: false,
  tracked: true,
  current_players: 1234,
  players_24h_delta: 56,
  review_count: 9000,
  twitch_viewers: 42,
  last_signal_at: "2026-07-12T14:00:00+00:00",
  steam_url: "https://store.steampowered.com/app/504230",
  breakdown: {
    score: 0.83,
    breakdown: {
      momentum: { weight: 0.4, value: 0.9, weighted: 0.36, reason: "rising fast" },
      hype: { weight: 0.2, value: 0.1, weighted: -0.05, reason: "cooling" },
      "penalty:cooldown": { multiplier: 0.5, reason: "recently streamed" },
    },
    created_at: "2026-07-12T12:00:00+00:00",
  },
  news: [
    {
      id: 10,
      title: "Celeste gets a surprise update",
      url: "https://example.com/celeste",
      source: "pcgamer",
      published_at: "2026-07-11T10:00:00+00:00",
    },
  ],
  similar: [{ id: 2, name: "Hades", genres: ["Roguelike"], current_players: 8800 }],
};

export const usersFixture: UserProfile[] = [
  {
    key: "default",
    label: "Legacy profile",
    liked_genres: [],
    blocked_genres: [],
    subscribed_genres: ["Puzzle"],
    muted_count: 1,
    digest_enabled: true,
    created_at: "2026-01-01T00:00:00+00:00",
  },
  {
    key: "123456",
    label: "Streamer Bob",
    liked_genres: ["Action"],
    blocked_genres: ["Horror"],
    subscribed_genres: [],
    muted_count: 0,
    digest_enabled: false,
    created_at: "2026-05-01T00:00:00+00:00",
  },
];

export const recommendationsPage1: Recommendation[] = [
  {
    id: 100,
    game_id: 1,
    game_name: "Celeste",
    score: 0.91,
    user_key: "default",
    created_at: "2026-07-12T12:00:00+00:00",
    sent_at: "2026-07-12T12:01:00+00:00",
    feedback: { up: 2, down: 0, played: 1 },
    breakdown: { momentum: { weighted: 0.3, reason: "surging" } },
  },
  {
    id: 101,
    game_id: 2,
    game_name: "Hades",
    score: 0.7,
    user_key: "default",
    created_at: "2026-07-12T12:00:00+00:00",
    sent_at: null,
    feedback: { up: 0, down: 0, played: 0 },
    breakdown: {},
  },
];

export const recommendationsPage2: Recommendation[] = [
  {
    id: 102,
    game_id: 3,
    game_name: "Dota 2",
    score: 0.62,
    user_key: "default",
    created_at: "2026-07-11T09:00:00+00:00",
    sent_at: "2026-07-11T09:01:00+00:00",
    feedback: { up: 1, down: 1, played: 0 },
    breakdown: {},
  },
];

export const refreshedPicks: Recommendation[] = [
  {
    id: null,
    game_id: 3,
    game_name: "Dota 2",
    score: 0.95,
    user_key: "default",
    created_at: null,
    sent_at: null,
    feedback: { up: 0, down: 0, played: 0 },
    breakdown: { momentum: { weighted: 0.4, reason: "just refreshed" } },
  },
];

export const newsPage1: NewsItem[] = [
  {
    id: 1,
    title: "Big Patch Lands",
    url: "https://example.com/patch",
    source: "pcgamer",
    published_at: "2026-07-12T12:00:00+00:00",
    game_id: 1,
    game_name: "Celeste",
    cluster_id: 5,
    similar_count: 1,
    similar: [
      {
        id: 2,
        title: "Same story, other site",
        source: "rps",
        url: "https://example.com/other",
      },
    ],
  },
];

export const newsPage2: NewsItem[] = [
  {
    id: 3,
    title: "Older headline",
    url: "https://example.com/older",
    source: "eurogamer",
    published_at: "2026-07-10T08:00:00+00:00",
    game_id: null,
    game_name: null,
    cluster_id: null,
    similar_count: 0,
    similar: [],
  },
];

export const newsSourcesFixture = ["pcgamer", "rps", "eurogamer"];
export const genresFixture = ["Action", "MOBA", "Platformer", "Roguelike"];

export const statusFixture: StatusPayload = {
  generated_at: "2026-07-12T14:00:00+00:00",
  sources: [
    {
      source: "steam_api",
      last_run_at: "2026-07-12T13:55:00+00:00",
      last_success_at: "2026-07-12T13:55:00+00:00",
    },
    {
      source: "rss",
      last_run_at: "2026-07-10T00:00:00+00:00",
      last_success_at: null,
    },
  ],
  stale_sources: ["rss"],
  counts: { games: 320, news: 1500, signals: 42000, recommendations: 88 },
  recent_recommendations: [{ name: "Celeste", score: 0.91, created_at: "2026-07-12T12:00:00+00:00" }],
};

export const dashboardFixture: DashboardPayload = {
  top_movers: [
    { game_id: 1, name: "Celeste", latest: 1234, delta: 56, pct: 4.8 },
    { game_id: 2, name: "Hades", latest: 8800, delta: -120, pct: -1.3 },
  ],
  latest_recommendations: [
    {
      id: 100,
      game_id: 1,
      game_name: "Celeste",
      score: 0.91,
      user_key: "default",
      created_at: "2026-07-12T12:00:00+00:00",
    },
  ],
  last_digest: { channel: "telegram_group", sent_at: "2026-07-12T16:00:00+00:00" },
  next_digest_at: "2026-07-13T16:00:00+00:00",
};

export const sourcesFixture: SourcesPayload = {
  sources: [
    {
      source: "rss",
      last_run_at: "2026-07-12T13:00:00+00:00",
      last_success_at: null,
      stale: true,
      jobs: [
        {
          id: 1,
          status: "error",
          started_at: "2026-07-12T13:00:00+00:00",
          finished_at: "2026-07-12T13:00:05+00:00",
          duration_s: 5,
          emitted: 10,
          written: 7,
          error: "HTTPError: boom",
        },
      ],
    },
    {
      source: "steam_api",
      last_run_at: "2026-07-12T13:55:00+00:00",
      last_success_at: "2026-07-12T13:55:00+00:00",
      stale: false,
      jobs: [
        {
          id: 2,
          status: "ok",
          started_at: "2026-07-12T13:55:00+00:00",
          finished_at: "2026-07-12T13:55:12+00:00",
          duration_s: 12,
          emitted: 300,
          written: 300,
          error: null,
        },
      ],
    },
  ],
  events_per_day: [
    { day: "2026-07-11", samples: 3000, news: 40, games: 12 },
    { day: "2026-07-12", samples: 3200, news: 44, games: 14 },
  ],
};

export const seriesFixture = {
  ts: [1_752_000_000, 1_752_086_400, 1_752_172_800],
  values: [1000, 1100, 1234],
};
