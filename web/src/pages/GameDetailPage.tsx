import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { fetchGame, fetchSeries } from "../api/games";
import type { SeriesMetric, SeriesRange } from "../api/types";
import { Chart } from "../components/Chart";
import { ScoreBars } from "../components/ScoreBars";
import { ErrorState, Loading } from "../components/States";
import {
  absoluteTime,
  compactNumber,
  formatPrice,
  formatScore,
  relativeTime,
  signedCompact,
} from "../lib/format";

const METRICS: { value: SeriesMetric; label: string }[] = [
  { value: "players", label: "Players" },
  { value: "review_count", label: "Reviews" },
  { value: "news_count", label: "News" },
  { value: "twitch_viewers", label: "Twitch" },
  { value: "price_cents", label: "Price" },
];
const RANGES: SeriesRange[] = ["7d", "30d", "90d"];

export function GameDetailPage() {
  const { id } = useParams();
  const gameId = Number(id);
  const [metric, setMetric] = useState<SeriesMetric>("players");
  const [range, setRange] = useState<SeriesRange>("7d");

  const game = useQuery({
    queryKey: ["game", gameId],
    queryFn: ({ signal }) => fetchGame(gameId, signal),
    enabled: Number.isFinite(gameId),
  });

  const series = useQuery({
    queryKey: ["series", gameId, metric, range],
    queryFn: ({ signal }) => fetchSeries(gameId, metric, range, signal),
    enabled: Number.isFinite(gameId) && game.isSuccess,
  });

  if (!Number.isFinite(gameId)) {
    return <ErrorState error={new Error("Invalid game id")} />;
  }
  if (game.isLoading) return <Loading label="Loading game…" />;
  if (game.isError) {
    const notFound = game.error instanceof ApiError && game.error.status === 404;
    return (
      <div className="state error">
        <h1>{notFound ? "Game not found" : "Failed to load game"}</h1>
        {!notFound && <p>{(game.error as Error).message}</p>}
        <Link to="/games" className="btn">
          Back to games
        </Link>
      </div>
    );
  }

  const g = game.data;
  if (!g) return <Loading label="Loading game…" />;

  return (
    <div>
      <p className="muted">
        <Link to="/games">← Games</Link>
      </p>
      <h1>{g.name}</h1>
      <div className="detail-meta">
        <span className="badge">{g.platform}</span>
        {g.tracked && <span className="badge accent">tracked</span>}
        {g.genres.map((genre) => (
          <span className="badge" key={genre}>
            {genre}
          </span>
        ))}
        <span className="price">{formatPrice(g.price_cents, g.is_free)}</span>
        {g.release_date && <span>released {absoluteTime(g.release_date)}</span>}
        {g.steam_url && (
          <a href={g.steam_url} target="_blank" rel="noopener noreferrer">
            Steam store ↗
          </a>
        )}
      </div>

      <div className="stat-row">
        <Stat label="Players now" value={compactNumber(g.current_players)} />
        <Stat
          label="24h Δ"
          value={signedCompact(g.players_24h_delta)}
          tone={(g.players_24h_delta ?? 0) >= 0 ? "pos" : "neg"}
        />
        <Stat label="Reviews" value={compactNumber(g.review_count)} />
        {g.twitch_viewers !== null && (
          <Stat label="Twitch" value={compactNumber(g.twitch_viewers)} />
        )}
        <Stat label="Last signal" value={relativeTime(g.last_signal_at)} />
      </div>

      {/* Chart with metric + range switcher */}
      <section className="section">
        <h2>Trends</h2>
        <div className="row" style={{ marginBottom: "0.6rem" }}>
          <div className="chips" role="group" aria-label="Metric">
            {METRICS.map((m) => (
              <button
                key={m.value}
                type="button"
                className={`chip ${metric === m.value ? "active" : ""}`}
                onClick={() => setMetric(m.value)}
              >
                {m.label}
              </button>
            ))}
          </div>
          <div className="chips" role="group" aria-label="Range">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                className={`chip ${range === r ? "active" : ""}`}
                onClick={() => setRange(r)}
              >
                {r}
              </button>
            ))}
          </div>
        </div>
        {series.isLoading ? (
          <Loading label="Loading series…" />
        ) : series.isError ? (
          <ErrorState error={series.error} onRetry={() => series.refetch()} />
        ) : series.data && series.data.ts.length > 1 ? (
          <Chart
            ts={series.data.ts}
            values={series.data.values}
            label={METRICS.find((m) => m.value === metric)?.label ?? metric}
          />
        ) : (
          <p className="muted">Not enough data points for this range.</p>
        )}
      </section>

      {/* Score breakdown */}
      <section className="section">
        <h2>
          Score breakdown{" "}
          {g.breakdown && <span className="rec-score">{formatScore(g.breakdown.score)}</span>}
        </h2>
        <ScoreBars breakdown={g.breakdown?.breakdown} />
        {g.breakdown?.created_at && (
          <p className="muted" style={{ marginTop: "0.4rem" }}>
            scored {relativeTime(g.breakdown.created_at)}
          </p>
        )}
      </section>

      {/* News */}
      <section className="section">
        <h2>News</h2>
        {g.news.length === 0 ? (
          <p className="muted">No news linked to this game.</p>
        ) : (
          g.news.map((n) => (
            <div className="news-card" key={n.id}>
              {n.url ? (
                <a className="news-title" href={n.url} target="_blank" rel="noopener noreferrer">
                  {n.title}
                </a>
              ) : (
                <span className="news-title">{n.title}</span>
              )}
              <div className="news-meta">
                <span>{n.source}</span>
                <span>{relativeTime(n.published_at)}</span>
              </div>
            </div>
          ))
        )}
      </section>

      {/* Similar */}
      <section className="section">
        <h2>Similar games</h2>
        {g.similar.length === 0 ? (
          <p className="muted">No similar games computed yet.</p>
        ) : (
          <div className="chips">
            {g.similar.map((sim) => (
              <Link className="chip" to={`/games/${sim.id}`} key={sim.id}>
                {sim.name}
              </Link>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="stat">
      <span className={`value ${tone ?? ""}`}>{value}</span>
      <span className="label">{label}</span>
    </div>
  );
}
