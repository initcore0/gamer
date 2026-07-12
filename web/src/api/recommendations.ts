import { apiGet, apiPost } from "./client";
import type { RecommendationsPage, RefreshResponse } from "./types";

export interface RecommendationsQuery {
  userKey?: string;
  cursor?: string | null;
  limit?: number;
}

export function fetchRecommendations(
  query: RecommendationsQuery,
  signal?: AbortSignal,
): Promise<RecommendationsPage> {
  return apiGet<RecommendationsPage>(
    "/recommendations",
    { user_key: query.userKey, cursor: query.cursor, limit: query.limit },
    signal,
  );
}

export function refreshRecommendations(
  userKey: string,
  limit = 10,
  signal?: AbortSignal,
): Promise<RefreshResponse> {
  return apiPost<RefreshResponse>(
    "/recommendations/refresh",
    { user_key: userKey, limit },
    signal,
  );
}
