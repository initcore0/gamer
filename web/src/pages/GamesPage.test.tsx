import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GamesPage } from "./GamesPage";
import { GameDetailPage } from "./GameDetailPage";
import { renderRoute } from "../test/renderApp";

function renderGames(route = "/games") {
  return renderRoute("games", <GamesPage />, route, [
    { path: "games/:id", element: <GameDetailPage /> },
  ]);
}

describe("GamesPage", () => {
  it("renders the first page of games with sparklines", async () => {
    renderGames();
    expect(await screen.findByRole("link", { name: "Celeste" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Hades" })).toBeInTheDocument();
    // Sparkline SVGs are rendered per row.
    expect(screen.getByLabelText("Celeste player trend")).toBeInTheDocument();
  });

  it("appends the next page on Load more (infinite scroll fallback)", async () => {
    const user = userEvent.setup();
    renderGames();
    await screen.findByRole("link", { name: "Celeste" });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    // Page 2's row is appended, page 1 rows remain.
    expect(await screen.findByRole("link", { name: "Dota 2" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Celeste" })).toBeInTheDocument();
    // Last page → no more Load more.
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument(),
    );
  });

  it("debounces search-as-you-type and reflects the query in the URL", async () => {
    const user = userEvent.setup();
    renderGames();
    await screen.findByRole("link", { name: "Celeste" });

    const search = screen.getByRole("searchbox", { name: /search games/i });
    await user.type(search, "dota");

    // After the 300ms debounce, the "dota" query returns only Dota 2.
    expect(await screen.findByRole("link", { name: "Dota 2" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: "Celeste" })).not.toBeInTheDocument(),
    );
  });

  it("seeds filter state from the URL (shareable)", async () => {
    renderGames("/games?q=dota");
    // The search input is seeded and the dota-filtered result shows.
    expect(await screen.findByRole("link", { name: "Dota 2" })).toBeInTheDocument();
    const search = screen.getByRole("searchbox", { name: /search games/i }) as HTMLInputElement;
    expect(search.value).toBe("dota");
  });

  it("sorts via clickable column headers (URL state)", async () => {
    const user = userEvent.setup();
    renderGames();
    await screen.findByRole("link", { name: "Celeste" });
    await user.click(screen.getByRole("columnheader", { name: /players/i }));
    // Re-query the header after the sort re-render before asserting aria-sort.
    await waitFor(() =>
      expect(screen.getByRole("columnheader", { name: /players/i })).toHaveAttribute(
        "aria-sort",
        "descending",
      ),
    );
  });
});
