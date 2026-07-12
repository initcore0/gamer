import { apiGet } from "./client";
import type { NewsPage, NewsSourcesResponse } from "./types";

export interface NewsQuery {
  source?: string;
  gameId?: number | null;
  cursor?: string | null;
  limit?: number;
}

export function fetchNews(query: NewsQuery, signal?: AbortSignal): Promise<NewsPage> {
  return apiGet<NewsPage>(
    "/news",
    {
      source: query.source,
      game_id: query.gameId,
      cursor: query.cursor,
      limit: query.limit,
    },
    signal,
  );
}

export async function fetchNewsSources(signal?: AbortSignal): Promise<string[]> {
  const res = await apiGet<NewsSourcesResponse>("/news/sources", undefined, signal);
  return res.sources;
}
