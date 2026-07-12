import { http, HttpResponse } from "msw";
import type { RefreshResponse } from "../api/types";
import {
  dashboardFixture,
  gameDetailFixture,
  gamesPage1,
  gamesPage2,
  genresFixture,
  newsPage1,
  newsPage2,
  newsSourcesFixture,
  recommendationsPage1,
  recommendationsPage2,
  refreshedPicks,
  seriesFixture,
  sourcesFixture,
  statusFixture,
  usersFixture,
} from "./fixtures";

const API = "/api/v1";

/** Tests can flip this to make the refresh feed return the fresh picks on the
 * subsequent GET, proving invalidation re-fetches. */
export const state = { refreshed: false };

export function resetState() {
  state.refreshed = false;
}

export const handlers = [
  // ── Catalog ───────────────────────────────────────────────────────────────
  http.get(`${API}/games`, ({ request }) => {
    const url = new URL(request.url);
    const q = url.searchParams.get("q");
    const cursor = url.searchParams.get("cursor");

    // Search: a "dota" query returns only the matching page-2 fixture (page 1),
    // no next cursor — exercises the debounced search path.
    if (q && q.toLowerCase().includes("dota")) {
      return HttpResponse.json({ games: gamesPage2, next_cursor: null });
    }

    if (cursor === "CURSOR_2") {
      return HttpResponse.json({ games: gamesPage2, next_cursor: null });
    }
    return HttpResponse.json({ games: gamesPage1, next_cursor: "CURSOR_2" });
  }),

  http.get(`${API}/genres`, () => HttpResponse.json({ genres: genresFixture })),

  http.get(`${API}/games/:id/series`, () =>
    HttpResponse.json(seriesFixture, {
      headers: { "Cache-Control": "public, max-age=300" },
    }),
  ),

  http.get(`${API}/games/:id`, ({ params }) => {
    if (params.id === "999") {
      return HttpResponse.json({ detail: "game not found" }, { status: 404 });
    }
    return HttpResponse.json({ ...gameDetailFixture, id: Number(params.id) });
  }),

  // ── Recommendations ─────────────────────────────────────────────────────────
  http.get(`${API}/recommendations`, ({ request }) => {
    const url = new URL(request.url);
    const cursor = url.searchParams.get("cursor");
    if (cursor === "REC_CURSOR_2") {
      return HttpResponse.json({ recommendations: recommendationsPage2, next_cursor: null });
    }
    const first = state.refreshed
      ? [...refreshedPicks, ...recommendationsPage1]
      : recommendationsPage1;
    return HttpResponse.json({ recommendations: first, next_cursor: "REC_CURSOR_2" });
  }),

  http.post(`${API}/recommendations/refresh`, async ({ request }) => {
    const body = (await request.json()) as { user_key?: string; limit?: number };
    if (body?.user_key && body.user_key !== "default" && body.user_key !== "123456") {
      return HttpResponse.json(
        { detail: `unknown user_key: ${body.user_key}` },
        { status: 422 },
      );
    }
    state.refreshed = true;
    return HttpResponse.json<RefreshResponse>({ recommendations: refreshedPicks });
  }),

  // ── Users ────────────────────────────────────────────────────────────────────
  http.get(`${API}/users`, () => HttpResponse.json({ users: usersFixture })),

  // ── News ──────────────────────────────────────────────────────────────────────
  http.get(`${API}/news/sources`, () => HttpResponse.json({ sources: newsSourcesFixture })),

  http.get(`${API}/news`, ({ request }) => {
    const url = new URL(request.url);
    const cursor = url.searchParams.get("cursor");
    const source = url.searchParams.get("source");
    if (cursor === "NEWS_CURSOR_2") {
      return HttpResponse.json({ news: newsPage2, next_cursor: null });
    }
    if (source === "eurogamer") {
      return HttpResponse.json({ news: newsPage2, next_cursor: null });
    }
    return HttpResponse.json({ news: newsPage1, next_cursor: "NEWS_CURSOR_2" });
  }),

  // ── Ops ────────────────────────────────────────────────────────────────────────
  http.get(`${API}/status`, () => HttpResponse.json(statusFixture)),
  http.get(`${API}/dashboard`, () => HttpResponse.json(dashboardFixture)),
  http.get(`${API}/sources`, () => HttpResponse.json(sourcesFixture)),
];
