// Types transcribed verbatim from API_CONTRACT.md — the single source of truth
// for every request/response shape. Keep in lockstep with that document.

export type Platform = "steam" | "xbox" | "psn" | "switch";
export type GameSort = "name" | "players" | "delta" | "reviews";
export type SeriesMetric =
  | "players"
  | "review_count"
  | "news_count"
  | "twitch_viewers"
  | "price_cents";
export type SeriesRange = "7d" | "30d" | "90d";

/** A weighted score component or a penalty, as stored in the breakdown jsonb. */
export interface BreakdownPart {
  weighted?: number;
  weight?: number;
  value?: number;
  reason?: string;
  multiplier?: number;
}
export type BreakdownMap = Record<string, BreakdownPart>;

// ── Catalog ────────────────────────────────────────────────────────────────

export interface GameListItem {
  id: number;
  name: string;
  platform: Platform;
  genres: string[];
  tracked: boolean;
  current_players: number | null;
  players_24h_delta: number | null;
  spark: number[];
  review_count: number | null;
  last_signal_at: string | null;
}

export interface GamesPage {
  games: GameListItem[];
  next_cursor: string | null;
}

export interface ScoreBreakdown {
  score: number;
  breakdown: BreakdownMap;
  created_at: string | null;
}

export interface GameNews {
  id: number;
  title: string;
  url: string | null;
  source: string;
  published_at: string | null;
}

export interface SimilarGame {
  id: number;
  name: string;
  genres: string[];
  current_players: number;
}

export interface GameDetail {
  id: number;
  name: string;
  platform: Platform;
  platform_app_id: number;
  genres: string[];
  release_date: string | null;
  price_cents: number | null;
  is_free: boolean;
  tracked: boolean;
  current_players: number | null;
  players_24h_delta: number | null;
  review_count: number | null;
  twitch_viewers: number | null;
  last_signal_at: string | null;
  steam_url: string | null;
  breakdown: ScoreBreakdown | null;
  news: GameNews[];
  similar: SimilarGame[];
}

export interface Series {
  ts: number[];
  values: number[];
}

export interface GenresResponse {
  genres: string[];
}

// ── Recommendations ──────────────────────────────────────────────────────────

export interface FeedbackCounts {
  up: number;
  down: number;
  played: number;
}

export interface Recommendation {
  id: number | null;
  game_id: number;
  game_name: string;
  score: number;
  user_key: string;
  created_at: string | null;
  sent_at: string | null;
  feedback: FeedbackCounts;
  breakdown: BreakdownMap;
}

export interface RecommendationsPage {
  recommendations: Recommendation[];
  next_cursor: string | null;
}

export interface RefreshResponse {
  recommendations: Recommendation[];
}

// ── Users / profiles ─────────────────────────────────────────────────────────

export interface UserProfile {
  key: string;
  label: string;
  liked_genres: string[];
  blocked_genres: string[];
  subscribed_genres: string[];
  muted_count: number;
  digest_enabled: boolean;
  created_at: string | null;
}

export interface UsersResponse {
  users: UserProfile[];
}

// ── News ─────────────────────────────────────────────────────────────────────

export interface NewsSimilar {
  id: number;
  title: string;
  source: string;
  url: string | null;
}

export interface NewsItem {
  id: number;
  title: string;
  url: string | null;
  source: string;
  published_at: string | null;
  game_id: number | null;
  game_name: string | null;
  cluster_id: number | null;
  similar_count: number;
  similar: NewsSimilar[];
}

export interface NewsPage {
  news: NewsItem[];
  next_cursor: string | null;
}

export interface NewsSourcesResponse {
  sources: string[];
}

// ── Ops ──────────────────────────────────────────────────────────────────────

export interface SourceStatus {
  source: string;
  last_run_at: string | null;
  last_success_at: string | null;
}

export interface StatusCounts {
  games: number;
  news: number;
  signals: number;
  recommendations: number;
}

export interface RecentRecommendation {
  name: string;
  score: number;
  created_at: string | null;
}

export interface StatusPayload {
  generated_at: string;
  sources: SourceStatus[];
  stale_sources: string[];
  counts: StatusCounts;
  recent_recommendations: RecentRecommendation[];
}

export interface TopMover {
  game_id: number;
  name: string;
  latest: number;
  delta: number;
  pct: number;
}

export interface DashboardRecommendation {
  id: number | null;
  game_id: number;
  game_name: string;
  score: number;
  user_key: string;
  created_at: string | null;
}

export interface LastDigest {
  channel: string;
  sent_at: string | null;
}

export interface DashboardPayload {
  top_movers: TopMover[];
  latest_recommendations: DashboardRecommendation[];
  last_digest: LastDigest | null;
  next_digest_at: string | null;
}

export interface SourceJob {
  id: number;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  emitted: number | null;
  written: number | null;
  error: string | null;
}

export interface SourceCard {
  source: string;
  last_run_at: string | null;
  last_success_at: string | null;
  stale: boolean;
  jobs: SourceJob[];
}

export interface DayCounts {
  day: string;
  samples: number;
  news: number;
  games: number;
}

export interface SourcesPayload {
  sources: SourceCard[];
  events_per_day: DayCounts[];
}
