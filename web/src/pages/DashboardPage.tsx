import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchDashboard, fetchStatus } from "../api/ops";
import type { SourceStatus } from "../api/types";
import { ErrorState, Loading } from "../components/States";
import {
  absoluteTime,
  compactNumber,
  formatScore,
  isOlderThan,
  relativeTime,
  signedCompact,
} from "../lib/format";

const STALE_HOURS = 24;

function freshnessOf(source: SourceStatus): { stale: boolean; live: boolean } {
  const stale = isOlderThan(source.last_success_at, STALE_HOURS);
  const live = !stale && !isOlderThan(source.last_success_at, 2);
  return { stale, live };
}

export function DashboardPage() {
  const status = useQuery({ queryKey: ["status"], queryFn: ({ signal }) => fetchStatus(signal) });
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: ({ signal }) => fetchDashboard(signal),
  });

  if (status.isError) return <ErrorState error={status.error} onRetry={() => status.refetch()} />;

  const s = status.data;
  // Guard on the data itself, not just isLoading — covers refetch/paused windows
  // where the query is neither loading nor errored but data is not yet present.
  if (!s) return <Loading label="Loading dashboard…" />;
  const d = dashboard.data;
  const staleSet = new Set(s.stale_sources);

  return (
    <div>
      <h1>Dashboard</h1>

      {/* Sync freshness FIRST — the whole point of the redesign. */}
      <section className="section" aria-labelledby="freshness-h">
        <h2 id="freshness-h">Sync freshness</h2>
        {s.stale_sources.length > 0 && (
          <p className="badge stale" role="status">
            {s.stale_sources.length} source{s.stale_sources.length > 1 ? "s" : ""} stale (&gt;
            {STALE_HOURS}h)
          </p>
        )}
        <div className="freshness-grid">
          {s.sources.map((src) => {
            const isStale = staleSet.has(src.source) || freshnessOf(src).stale;
            const isLive = freshnessOf(src).live && !isStale;
            return (
              <div className={`freshness ${isStale ? "stale" : ""}`} key={src.source}>
                <div className="name">
                  <span>{src.source}</span>
                  <span className={`badge ${isStale ? "stale" : isLive ? "live" : ""}`}>
                    {isStale ? "STALE" : isLive ? "LIVE" : "ok"}
                  </span>
                </div>
                <span className="muted" title={absoluteTime(src.last_success_at)}>
                  success {relativeTime(src.last_success_at)}
                </span>
                <span className="muted" title={absoluteTime(src.last_run_at)}>
                  last run {relativeTime(src.last_run_at)}
                </span>
              </div>
            );
          })}
          {s.sources.length === 0 && <p className="muted">No sources reporting yet.</p>}
        </div>
      </section>

      {/* Status counts */}
      <section className="section">
        <h2>Counts</h2>
        <div className="tiles">
          <div className="tile">
            <span className="tile-num">{compactNumber(s.counts.games)}</span>
            <span className="tile-label">games</span>
          </div>
          <div className="tile">
            <span className="tile-num">{compactNumber(s.counts.news)}</span>
            <span className="tile-label">news</span>
          </div>
          <div className="tile">
            <span className="tile-num">{compactNumber(s.counts.signals)}</span>
            <span className="tile-label">signals</span>
          </div>
          <div className="tile">
            <span className="tile-num">{compactNumber(s.counts.recommendations)}</span>
            <span className="tile-label">recommendations</span>
          </div>
        </div>
      </section>

      {/* Top movers */}
      <section className="section">
        <h2>Top movers (24h)</h2>
        {dashboard.isLoading && <Loading label="Loading movers…" />}
        {dashboard.isError && <ErrorState error={dashboard.error} onRetry={() => dashboard.refetch()} />}
        {d && d.top_movers.length > 0 ? (
          <div className="movers-strip">
            {d.top_movers.map((m) => (
              <Link className="mover" to={`/games/${m.game_id}`} key={m.game_id}>
                <span className="mover-name">{m.name}</span>
                <span className={m.delta >= 0 ? "pos" : "neg"}>{signedCompact(m.delta)}</span>
                <span className="muted">{compactNumber(m.latest)} now</span>
              </Link>
            ))}
          </div>
        ) : (
          d && <p className="muted">No movers yet.</p>
        )}
      </section>

      {/* Latest recommendations strip */}
      <section className="section">
        <h2>Latest recommendations</h2>
        {d && d.latest_recommendations.length > 0 ? (
          <div className="strip">
            {d.latest_recommendations.map((r, i) => (
              <Link className="rec-pill" to={`/games/${r.game_id}`} key={r.id ?? `${r.game_id}-${i}`}>
                <span>{r.game_name}</span>
                <span className="rec-score">{formatScore(r.score)}</span>
              </Link>
            ))}
          </div>
        ) : (
          d && <p className="muted">No recommendations yet.</p>
        )}
        <p className="muted" style={{ marginTop: "0.6rem" }}>
          <Link to="/recommendations">See the full feed →</Link>
        </p>
      </section>

      {/* Digest state */}
      <section className="section">
        <h2>Digest</h2>
        <div className="digest-line">
          <span>
            Last digest:{" "}
            {d?.last_digest ? (
              <span title={absoluteTime(d.last_digest.sent_at)}>
                {d.last_digest.channel} · {relativeTime(d.last_digest.sent_at)}
              </span>
            ) : (
              <span className="muted">none sent</span>
            )}
          </span>
          <span>
            Next digest:{" "}
            <span title={absoluteTime(d?.next_digest_at)}>
              {d?.next_digest_at ? relativeTime(d.next_digest_at) : "—"}
            </span>
          </span>
        </div>
      </section>
    </div>
  );
}
