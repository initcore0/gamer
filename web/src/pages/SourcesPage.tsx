import { useQuery } from "@tanstack/react-query";
import { fetchSources } from "../api/ops";
import type { DayCounts, SourceCard } from "../api/types";
import { ErrorState, Loading } from "../components/States";
import { absoluteTime, compactNumber, relativeTime } from "../lib/format";

export function SourcesPage() {
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: ({ signal }) => fetchSources(signal),
  });

  if (sources.isError)
    return <ErrorState error={sources.error} onRetry={() => sources.refetch()} />;
  if (!sources.data) return <Loading label="Loading sources…" />;

  const { sources: cards, events_per_day: events } = sources.data;

  return (
    <div>
      <h1>Sources</h1>

      <section className="section">
        <h2>Events per day (14d)</h2>
        <EventsBars events={events} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Day</th>
                <th className="num">Samples</th>
                <th className="num">News</th>
                <th className="num">Games</th>
              </tr>
            </thead>
            <tbody>
              {events.map((d) => (
                <tr key={d.day}>
                  <td>{d.day}</td>
                  <td className="num">{compactNumber(d.samples)}</td>
                  <td className="num">{compactNumber(d.news)}</td>
                  <td className="num">{compactNumber(d.games)}</td>
                </tr>
              ))}
              {events.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No events recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Jobs by source</h2>
        {cards.length === 0 && <p className="muted">No sources have run yet.</p>}
        {cards.map((card) => (
          <SourceCardView card={card} key={card.source} />
        ))}
      </section>
    </div>
  );
}

function EventsBars({ events }: { events: DayCounts[] }) {
  if (events.length === 0) return null;
  const max = Math.max(1, ...events.map((d) => d.samples));
  return (
    <div className="events-bars" role="img" aria-label="Samples per day, last 14 days">
      {events.map((d) => (
        <div
          className="bar"
          key={d.day}
          style={{ height: `${Math.max(4, (d.samples / max) * 100)}%` }}
          title={`${d.day}: ${d.samples} samples`}
        />
      ))}
    </div>
  );
}

function SourceCardView({ card }: { card: SourceCard }) {
  return (
    <div className={`source-card ${card.stale ? "stale" : ""}`}>
      <div className="source-head">
        <span className="name">{card.source}</span>
        {card.stale ? (
          <span className="badge stale">STALE</span>
        ) : (
          <span className="badge live">ok</span>
        )}
        <span className="muted" title={absoluteTime(card.last_success_at)}>
          success {relativeTime(card.last_success_at)}
        </span>
        <span className="muted" title={absoluteTime(card.last_run_at)}>
          run {relativeTime(card.last_run_at)}
        </span>
      </div>
      <div className="table-wrap">
        <table className="jobs-table">
          <thead>
            <tr>
              <th>Status</th>
              <th>Started</th>
              <th className="num">Duration</th>
              <th className="num">Emitted</th>
              <th className="num">Written</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {card.jobs.map((job) => (
              <tr key={job.id} className={job.status === "error" ? "job-error" : ""}>
                <td>{job.status}</td>
                <td className="muted" title={absoluteTime(job.started_at)}>
                  {relativeTime(job.started_at)}
                </td>
                <td className="num">{job.duration_s !== null ? `${job.duration_s.toFixed(1)}s` : "—"}</td>
                <td className="num">{job.emitted ?? "—"}</td>
                <td className="num">{job.written ?? "—"}</td>
                <td className="job-err">{job.error ?? ""}</td>
              </tr>
            ))}
            {card.jobs.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No jobs recorded.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
