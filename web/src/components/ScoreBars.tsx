import type { BreakdownMap } from "../api/types";

interface Bar {
  key: string;
  weighted: number;
  reason: string;
  widthPct: number;
  positive: boolean;
}
interface Penalty {
  key: string;
  multiplier: number | undefined;
  reason: string;
}

/** Split a breakdown jsonb into component bars + penalty rows (mirrors the
 * server-side `_breakdown_bars` / `_breakdown_penalties` shaping). */
export function shapeBreakdown(breakdown: BreakdownMap | null | undefined): {
  bars: Bar[];
  penalties: Penalty[];
} {
  if (!breakdown) return { bars: [], penalties: [] };
  const components = Object.entries(breakdown).filter(
    ([key, part]) => !key.startsWith("penalty:") && typeof part?.weighted === "number",
  );
  const maxAbs = Math.max(1e-9, ...components.map(([, p]) => Math.abs(p.weighted as number)));
  const bars: Bar[] = components.map(([key, part]) => {
    const weighted = part.weighted as number;
    return {
      key,
      weighted,
      reason: part.reason ?? "",
      widthPct: (Math.abs(weighted) / maxAbs) * 100,
      positive: weighted >= 0,
    };
  });
  const penalties: Penalty[] = Object.entries(breakdown)
    .filter(([key]) => key.startsWith("penalty:"))
    .map(([key, part]) => ({
      key: key.replace(/^penalty:/, ""),
      multiplier: typeof part.multiplier === "number" ? part.multiplier : undefined,
      reason: part.reason ?? "",
    }));
  return { bars, penalties };
}

export function ScoreBars({ breakdown }: { breakdown: BreakdownMap | null | undefined }) {
  const { bars, penalties } = shapeBreakdown(breakdown);
  if (bars.length === 0 && penalties.length === 0) {
    return <p className="muted">No score breakdown available.</p>;
  }
  return (
    <>
      <ul className="bars">
        {bars.map((bar) => (
          <li className="bar-row" key={bar.key}>
            <span className="bar-key">{bar.key}</span>
            <span className="bar-track">
              <span
                className={`bar-fill ${bar.positive ? "pos" : "neg"}`}
                style={{ width: `${bar.widthPct}%` }}
              />
            </span>
            <span className="bar-value">{bar.weighted.toFixed(3)}</span>
            {bar.reason && <span className="bar-reason">{bar.reason}</span>}
          </li>
        ))}
      </ul>
      {penalties.length > 0 && (
        <ul className="penalties">
          {penalties.map((p) => (
            <li key={p.key}>
              <span className="penalty-mult">×{p.multiplier ?? "?"}</span> {p.key}
              {p.reason ? ` — ${p.reason}` : ""}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
