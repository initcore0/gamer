// Small, dependency-free formatting helpers shared across pages.

/** Compact integer-ish number, e.g. 1234 → "1.2k", 5_600_000 → "5.6M". */
export function compactNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Math.round(value);
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

/** Signed compact delta, e.g. +56, -1.2k. */
export function signedCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${compactNumber(Math.abs(value))}`;
}

/** Score in [0,1] shown as a 2-decimal string. */
export function formatScore(score: number): string {
  return score.toFixed(2);
}

/** "$19.99" from integer cents; "Free" when free; "—" when unknown. */
export function formatPrice(cents: number | null | undefined, isFree?: boolean): string {
  if (isFree) return "Free";
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

/** Human relative time ("3 hours ago", "in 2 days"); "never" for null. */
export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diffSeconds = Math.round((then - now.getTime()) / 1000);
  const abs = Math.abs(diffSeconds);
  for (const [unit, secs] of UNITS) {
    if (abs >= secs || unit === "second") {
      return RELATIVE.format(Math.round(diffSeconds / secs), unit);
    }
  }
  return "just now";
}

/** Absolute short timestamp for titles/tooltips. */
export function absoluteTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** True when a timestamp is older than `hours` from `now`. */
export function isOlderThan(iso: string | null | undefined, hours: number, now = new Date()): boolean {
  if (!iso) return true;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return true;
  return now.getTime() - then > hours * 60 * 60 * 1000;
}
