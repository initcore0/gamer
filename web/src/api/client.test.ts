import { describe, expect, it } from "vitest";
import { ApiError, apiGet, buildUrl } from "./client";
import { fetchGames } from "./games";

describe("buildUrl", () => {
  it("drops empty-string, null, and undefined params (contract §Conventions)", () => {
    const url = buildUrl("/games", {
      q: "dota",
      platform: "",
      genre: undefined,
      tracked: null,
      sort: "name",
    });
    expect(url).toBe("/api/v1/games?q=dota&sort=name");
  });

  it("keeps zero and false-ish non-empty values", () => {
    expect(buildUrl("/news", { game_id: 0 })).toBe("/api/v1/news?game_id=0");
  });
});

describe("apiGet / cursor handling", () => {
  it("passes the opaque next_cursor back verbatim as `cursor`", async () => {
    // First page returns a cursor; second page (with that cursor) is the last.
    const page1 = await fetchGames({});
    expect(page1.next_cursor).toBe("CURSOR_2");
    const url = buildUrl("/games", { cursor: page1.next_cursor });
    expect(url).toBe("/api/v1/games?cursor=CURSOR_2");
    const page2 = await fetchGames({ cursor: page1.next_cursor });
    expect(page2.next_cursor).toBeNull();
    expect(page2.games[0]?.name).toBe("Dota 2");
  });

  it("throws a typed ApiError with detail on 404", async () => {
    await expect(apiGet("/games/999")).rejects.toBeInstanceOf(ApiError);
    try {
      await apiGet("/games/999");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(404);
      expect((err as ApiError).detail).toBe("game not found");
    }
  });
});
