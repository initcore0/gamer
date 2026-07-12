import { useMemo, useState } from "react";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { fetchRecommendations, refreshRecommendations } from "../api/recommendations";
import { fetchUsers } from "../api/users";
import type { Recommendation } from "../api/types";
import { ScoreBars } from "../components/ScoreBars";
import { Empty, ErrorState, Loading, SkeletonRows } from "../components/States";
import { absoluteTime, formatScore, relativeTime } from "../lib/format";
import { useIntersection } from "../lib/useIntersection";

/** Group a newest-first list into runs sharing the same created_at minute. */
function groupRuns(rows: Recommendation[]): { minute: string; rows: Recommendation[] }[] {
  const groups: { minute: string; rows: Recommendation[] }[] = [];
  for (const row of rows) {
    const minute = row.created_at ? row.created_at.slice(0, 16) : "unknown";
    const last = groups[groups.length - 1];
    if (last && last.minute === minute) last.rows.push(row);
    else groups.push({ minute, rows: [row] });
  }
  return groups;
}

export function RecommendationsPage() {
  const [params, setParams] = useSearchParams();
  const userKey = params.get("user") ?? "default";
  const queryClient = useQueryClient();
  const [refreshError, setRefreshError] = useState<string | null>(null);

  const users = useQuery({ queryKey: ["users"], queryFn: ({ signal }) => fetchUsers(signal) });

  const feed = useInfiniteQuery({
    queryKey: ["recommendations", userKey],
    queryFn: ({ pageParam, signal }) =>
      fetchRecommendations({ userKey, cursor: pageParam }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const refresh = useMutation({
    mutationFn: () => refreshRecommendations(userKey, 10),
    onMutate: () => setRefreshError(null),
    onSuccess: () => {
      // The refresh persisted fresh rows — invalidate the feed so it re-fetches.
      queryClient.invalidateQueries({ queryKey: ["recommendations", userKey] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (err) => setRefreshError(err instanceof Error ? err.message : "Refresh failed"),
  });

  const rows = feed.data?.pages.flatMap((p) => p.recommendations) ?? [];
  const groups = useMemo(() => groupRuns(rows), [rows]);
  const sentinelRef = useIntersection(
    () => feed.fetchNextPage(),
    feed.hasNextPage === true && !feed.isFetchingNextPage,
  );

  const selectProfile = (key: string) => {
    const next = new URLSearchParams(params);
    if (key === "default") next.delete("user");
    else next.set("user", key);
    setParams(next, { replace: true });
  };

  return (
    <div>
      <h1>Recommendations</h1>

      <div className="profile-switcher">
        <label htmlFor="profile" className="muted">
          Profile
        </label>
        <select
          id="profile"
          className="control"
          value={userKey}
          onChange={(e) => selectProfile(e.target.value)}
        >
          <option value="all">All profiles</option>
          {(users.data ?? [{ key: "default", label: "Legacy profile" }]).map((u) => (
            <option key={u.key} value={u.key}>
              {u.label} ({u.key})
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn primary"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending || userKey === "all"}
          title={userKey === "all" ? "Pick a single profile to refresh" : "Run the scorer now"}
        >
          {refresh.isPending ? "Refreshing…" : "Refresh picks"}
        </button>
      </div>
      {refreshError && (
        <p className="badge stale" role="alert">
          {refreshError}
        </p>
      )}

      {feed.isLoading ? (
        <SkeletonRows rows={6} />
      ) : feed.isError ? (
        <ErrorState error={feed.error} onRetry={() => feed.refetch()} />
      ) : rows.length === 0 ? (
        <Empty>No recommendations for this profile yet. Try “Refresh picks”.</Empty>
      ) : (
        groups.map((group) => (
          <div className="run-group" key={group.minute}>
            <h3 className="run-head" title={absoluteTime(group.rows[0]?.created_at)}>
              Run · {relativeTime(group.rows[0]?.created_at ?? null)} · {group.rows.length} pick
              {group.rows.length > 1 ? "s" : ""}
            </h3>
            {group.rows.map((rec) => (
              <details className="rec-row" key={rec.id ?? `${rec.game_id}-${rec.created_at}`}>
                <summary>
                  <Link className="rec-game" to={`/games/${rec.game_id}`}>
                    {rec.game_name}
                  </Link>
                  <span className="rec-score">{formatScore(rec.score)}</span>
                  {rec.sent_at ? (
                    <span className="badge accent">sent</span>
                  ) : (
                    <span className="badge">unsent</span>
                  )}
                  {userKey === "all" && <span className="badge">{rec.user_key}</span>}
                  <span className="rec-feedback">
                    👍 {rec.feedback.up} · 👎 {rec.feedback.down} · 🎮 {rec.feedback.played}
                  </span>
                </summary>
                <div style={{ padding: "0.5rem 0" }}>
                  <ScoreBars breakdown={rec.breakdown} />
                </div>
              </details>
            ))}
          </div>
        ))
      )}

      {feed.isFetchingNextPage && <Loading label="Loading more…" />}
      {feed.hasNextPage && !feed.isFetchingNextPage && (
        <div ref={sentinelRef} className="infinite-sentinel" aria-hidden="true" />
      )}
      {feed.hasNextPage && (
        <button
          type="button"
          className="btn load-more"
          onClick={() => feed.fetchNextPage()}
          disabled={feed.isFetchingNextPage}
        >
          Load more
        </button>
      )}
    </div>
  );
}
