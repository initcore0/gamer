// Thin fetch wrapper for the gamer JSON API. Same-origin in prod (the SPA is
// served by the backend) and via the Vite dev proxy in development, so no base
// URL is needed. Every resource module builds on `apiGet` / `apiPost`.

export const API_BASE = "/api/v1";

/** A non-2xx response, carrying the parsed FastAPI `{detail}` when present. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let detail: unknown = null;
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    detail = body?.detail ?? null;
    if (typeof body?.detail === "string") message = body.detail;
  } catch {
    // Non-JSON error body (e.g. an HTML 500 page) — keep the status message.
  }
  return new ApiError(res.status, detail, message);
}

/** Build `${API_BASE}${path}?${query}`, dropping empty-string / null params. */
export function buildUrl(path: string, params?: Record<string, unknown>): string {
  const url = new URL(`${API_BASE}${path}`, "http://local");
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      // Empty string and null/undefined mean "not provided" (contract §Conventions).
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return `${url.pathname}${url.search}`;
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  const res = await fetch(buildUrl(path, params), {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}
