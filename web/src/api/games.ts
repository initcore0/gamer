import { apiGet } from "./client";
import type {
  GameDetail,
  GamesPage,
  GameSort,
  GenresResponse,
  Platform,
  Series,
  SeriesMetric,
  SeriesRange,
} from "./types";

export interface GamesQuery {
  q?: string;
  platform?: Platform | "";
  genre?: string;
  tracked?: boolean;
  active?: boolean;
  sort?: GameSort;
  cursor?: string | null;
  limit?: number;
}

export function fetchGames(query: GamesQuery, signal?: AbortSignal): Promise<GamesPage> {
  return apiGet<GamesPage>(
    "/games",
    {
      q: query.q,
      platform: query.platform,
      genre: query.genre,
      // Booleans only sent when true — false is the "not filtered" default.
      tracked: query.tracked ? "true" : undefined,
      active: query.active ? "true" : undefined,
      sort: query.sort,
      cursor: query.cursor,
      limit: query.limit,
    },
    signal,
  );
}

export function fetchGame(id: number, signal?: AbortSignal): Promise<GameDetail> {
  return apiGet<GameDetail>(`/games/${id}`, undefined, signal);
}

export function fetchSeries(
  id: number,
  metric: SeriesMetric,
  range: SeriesRange,
  signal?: AbortSignal,
): Promise<Series> {
  return apiGet<Series>(`/games/${id}/series`, { metric, range }, signal);
}

export async function fetchGenres(signal?: AbortSignal): Promise<string[]> {
  const res = await apiGet<GenresResponse>("/genres", undefined, signal);
  return res.genres;
}
