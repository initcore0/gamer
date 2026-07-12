import type { ReactNode } from "react";
import { ApiError } from "../api/client";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <div className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message =
    error instanceof ApiError
      ? `${error.message}`
      : error instanceof Error
        ? error.message
        : "Something went wrong.";
  return (
    <div className="state error" role="alert">
      <p>Failed to load: {message}</p>
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state">{children}</div>;
}

/** A block of shimmer skeleton rows for first-load placeholders. */
export function SkeletonRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="grid" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skeleton" key={i} style={{ height: "1.6rem" }} />
      ))}
    </div>
  );
}
