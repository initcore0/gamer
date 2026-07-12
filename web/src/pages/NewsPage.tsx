import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { fetchNews, fetchNewsSources } from "../api/news";
import { Empty, ErrorState, Loading, SkeletonRows } from "../components/States";
import { relativeTime } from "../lib/format";
import { useIntersection } from "../lib/useIntersection";

export function NewsPage() {
  const [params, setParams] = useSearchParams();
  const source = params.get("source") ?? "";
  const gameIdRaw = params.get("game_id") ?? "";
  const gameId = gameIdRaw ? Number(gameIdRaw) : null;

  const sources = useQuery({
    queryKey: ["news-sources"],
    queryFn: ({ signal }) => fetchNewsSources(signal),
  });

  const news = useInfiniteQuery({
    queryKey: ["news", source, gameId],
    queryFn: ({ pageParam, signal }) =>
      fetchNews({ source: source || undefined, gameId, cursor: pageParam }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const cards = news.data?.pages.flatMap((p) => p.news) ?? [];
  const sentinelRef = useIntersection(
    () => news.fetchNextPage(),
    news.hasNextPage === true && !news.isFetchingNextPage,
  );

  const update = (patch: { source?: string; game_id?: string }) => {
    const next = new URLSearchParams(params);
    if (patch.source !== undefined) {
      patch.source ? next.set("source", patch.source) : next.delete("source");
    }
    if (patch.game_id !== undefined) {
      patch.game_id ? next.set("game_id", patch.game_id) : next.delete("game_id");
    }
    setParams(next, { replace: true });
  };

  return (
    <div>
      <h1>News</h1>

      <div className="filter-bar">
        <select
          className="control"
          aria-label="Source"
          value={source}
          onChange={(e) => update({ source: e.target.value })}
        >
          <option value="">All sources</option>
          {(sources.data ?? []).map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {gameId !== null && (
          <button type="button" className="chip active" onClick={() => update({ game_id: "" })}>
            game #{gameId} ✕
          </button>
        )}
      </div>

      {news.isLoading ? (
        <SkeletonRows rows={8} />
      ) : news.isError ? (
        <ErrorState error={news.error} onRetry={() => news.refetch()} />
      ) : cards.length === 0 ? (
        <Empty>No news matches these filters.</Empty>
      ) : (
        cards.map((card) => (
          <article className="news-card" key={card.id}>
            {card.url ? (
              <a className="news-title" href={card.url} target="_blank" rel="noopener noreferrer">
                {card.title}
              </a>
            ) : (
              <span className="news-title">{card.title}</span>
            )}
            <div className="news-meta">
              <span>{card.source}</span>
              <span>{relativeTime(card.published_at)}</span>
              {card.game_id && card.game_name && (
                <Link to={`/games/${card.game_id}`}>{card.game_name}</Link>
              )}
              {card.similar_count > 0 && (
                <span className="badge">+{card.similar_count} similar</span>
              )}
            </div>
            {card.similar.length > 0 && (
              <details className="news-similar">
                <summary>{card.similar_count} related stories</summary>
                <ul>
                  {card.similar.map((s) => (
                    <li key={s.id}>
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noopener noreferrer">
                          {s.title}
                        </a>
                      ) : (
                        s.title
                      )}{" "}
                      <span className="muted">— {s.source}</span>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </article>
        ))
      )}

      {news.isFetchingNextPage && <Loading label="Loading more…" />}
      {news.hasNextPage && !news.isFetchingNextPage && (
        <div ref={sentinelRef} className="infinite-sentinel" aria-hidden="true" />
      )}
      {news.hasNextPage && (
        <button
          type="button"
          className="btn load-more"
          onClick={() => news.fetchNextPage()}
          disabled={news.isFetchingNextPage}
        >
          Load more
        </button>
      )}
    </div>
  );
}
