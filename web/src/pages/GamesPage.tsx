import { useCallback, useEffect, useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { fetchGames, fetchGenres, type GamesQuery } from "../api/games";
import type { GameSort, Platform } from "../api/types";
import { Sparkline } from "../components/Sparkline";
import { Empty, ErrorState, Loading, SkeletonRows } from "../components/States";
import { compactNumber, relativeTime, signedCompact } from "../lib/format";
import { useDebounce } from "../lib/useDebounce";
import { useIntersection } from "../lib/useIntersection";

const PLATFORMS: Platform[] = ["steam", "xbox", "psn", "switch"];

/** Read filter state from the URL (shareable) with sane defaults. */
function useFilters() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(
    () => ({
      q: params.get("q") ?? "",
      platform: (params.get("platform") ?? "") as Platform | "",
      genre: params.get("genre") ?? "",
      tracked: params.get("tracked") === "1",
      active: params.get("active") === "1",
      sort: (params.get("sort") ?? "name") as GameSort,
    }),
    [params],
  );

  const update = useCallback(
    (patch: Partial<typeof filters>) => {
      const next = new URLSearchParams(params);
      const merged = { ...filters, ...patch };
      const set = (k: string, v: string, drop: boolean) =>
        drop ? next.delete(k) : next.set(k, v);
      set("q", merged.q, merged.q === "");
      set("platform", merged.platform, merged.platform === "");
      set("genre", merged.genre, merged.genre === "");
      set("tracked", "1", !merged.tracked);
      set("active", "1", !merged.active);
      set("sort", merged.sort, merged.sort === "name");
      setParams(next, { replace: true });
    },
    [filters, params, setParams],
  );

  return { filters, update };
}

export function GamesPage() {
  const { filters, update } = useFilters();
  // Local input mirrors the URL but drives a debounced query so typing cancels
  // in-flight requests (TanStack keeps only the latest query key active).
  const [searchInput, setSearchInput] = useState(filters.q);
  const debouncedSearch = useDebounce(searchInput, 300);

  // Push the debounced value into the URL (shareable), without clobbering while typing.
  useEffect(() => {
    if (debouncedSearch !== filters.q) update({ q: debouncedSearch });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const genres = useQuery({ queryKey: ["genres"], queryFn: ({ signal }) => fetchGenres(signal) });

  const query: GamesQuery = {
    q: debouncedSearch || undefined,
    platform: filters.platform || undefined,
    genre: filters.genre || undefined,
    tracked: filters.tracked || undefined,
    active: filters.active || undefined,
    sort: filters.sort,
  };

  const games = useInfiniteQuery({
    queryKey: ["games", query],
    queryFn: ({ pageParam, signal }) => fetchGames({ ...query, cursor: pageParam }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const rows = games.data?.pages.flatMap((p) => p.games) ?? [];
  const sentinelRef = useIntersection(
    () => games.fetchNextPage(),
    games.hasNextPage === true && !games.isFetchingNextPage,
  );

  const toggleSort = (col: GameSort) => update({ sort: col });

  return (
    <div>
      <h1>Games</h1>

      <div className="filter-bar">
        <input
          className="search"
          type="search"
          placeholder="Search games…"
          aria-label="Search games"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <select
          className="control"
          aria-label="Platform"
          value={filters.platform}
          onChange={(e) => update({ platform: e.target.value as Platform | "" })}
        >
          <option value="">All platforms</option>
          {PLATFORMS.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <select
          className="control"
          aria-label="Genre"
          value={filters.genre}
          onChange={(e) => update({ genre: e.target.value })}
        >
          <option value="">All genres</option>
          {(genres.data ?? []).map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        <label className="toggle">
          <input
            type="checkbox"
            checked={filters.tracked}
            onChange={(e) => update({ tracked: e.target.checked })}
          />
          Tracked
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={filters.active}
            onChange={(e) => update({ active: e.target.checked })}
          />
          Active
        </label>
      </div>

      {games.isLoading ? (
        <SkeletonRows rows={8} />
      ) : games.isError ? (
        <ErrorState error={games.error} onRetry={() => games.refetch()} />
      ) : rows.length === 0 ? (
        <Empty>No games match these filters.</Empty>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <SortHeader col="name" label="Name" active={filters.sort} onClick={toggleSort} />
                <th>Platform</th>
                <SortHeader
                  col="players"
                  label="Players"
                  active={filters.sort}
                  onClick={toggleSort}
                  numeric
                />
                <SortHeader
                  col="delta"
                  label="24h Δ"
                  active={filters.sort}
                  onClick={toggleSort}
                  numeric
                />
                <SortHeader
                  col="reviews"
                  label="Reviews"
                  active={filters.sort}
                  onClick={toggleSort}
                  numeric
                />
                <th>Trend</th>
                <th>Last signal</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((g) => (
                <tr key={g.id}>
                  <td>
                    <Link to={`/games/${g.id}`}>{g.name}</Link>
                    {g.tracked && <span className="badge accent"> tracked</span>}
                  </td>
                  <td>{g.platform}</td>
                  <td className="num">{compactNumber(g.current_players)}</td>
                  <td className={`num ${(g.players_24h_delta ?? 0) >= 0 ? "pos" : "neg"}`}>
                    {signedCompact(g.players_24h_delta)}
                  </td>
                  <td className="num">{compactNumber(g.review_count)}</td>
                  <td className="spark-cell">
                    <Sparkline points={g.spark} ariaLabel={`${g.name} player trend`} />
                  </td>
                  <td className="muted">{relativeTime(g.last_signal_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {games.isFetchingNextPage && <Loading label="Loading more…" />}
      {games.hasNextPage && !games.isFetchingNextPage && (
        <div ref={sentinelRef} className="infinite-sentinel" aria-hidden="true" />
      )}
      {games.hasNextPage && (
        <button
          type="button"
          className="btn load-more"
          onClick={() => games.fetchNextPage()}
          disabled={games.isFetchingNextPage}
        >
          Load more
        </button>
      )}
    </div>
  );
}

function SortHeader({
  col,
  label,
  active,
  onClick,
  numeric,
}: {
  col: GameSort;
  label: string;
  active: GameSort;
  onClick: (c: GameSort) => void;
  numeric?: boolean;
}) {
  return (
    <th
      className={`sortable ${numeric ? "num" : ""}`}
      onClick={() => onClick(col)}
      aria-sort={active === col ? "descending" : "none"}
    >
      {label}
      {active === col && <span className="sort-caret">▾</span>}
    </th>
  );
}
