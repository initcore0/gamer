import { afterEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecommendationsPage } from "./RecommendationsPage";
import { renderRoute } from "../test/renderApp";
import { resetState } from "../test/handlers";

afterEach(() => resetState());

function renderRecs(route = "/recommendations") {
  return renderRoute("recommendations", <RecommendationsPage />, route);
}

describe("RecommendationsPage", () => {
  it("groups the feed into runs with feedback counts", async () => {
    renderRecs();
    expect(await screen.findByRole("link", { name: "Celeste" })).toBeInTheDocument();
    // Feedback counts render on the summary row.
    expect(screen.getByText(/👍 2 · 👎 0 · 🎮 1/)).toBeInTheDocument();
    // Run header present.
    expect(screen.getAllByText(/Run ·/).length).toBeGreaterThan(0);
  });

  it("populates the profile switcher from /api/v1/users", async () => {
    renderRecs();
    await screen.findByRole("link", { name: "Celeste" });
    const select = screen.getByLabelText("Profile") as HTMLSelectElement;
    await waitFor(() =>
      expect(within(select).queryByText(/Streamer Bob/)).toBeInTheDocument(),
    );
  });

  it("refresh → POST → feed invalidation shows the fresh pick", async () => {
    const user = userEvent.setup();
    renderRecs();
    await screen.findByRole("link", { name: "Celeste" });
    // Dota 2 (the refreshed pick) is not present before refresh.
    expect(screen.queryByRole("link", { name: "Dota 2" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Refresh picks" }));

    // After the POST resolves, the feed is invalidated and re-fetched; the MSW
    // handler now prepends the refreshed pick.
    expect(await screen.findByRole("link", { name: "Dota 2" })).toBeInTheDocument();
  });

  it("expands a row to reveal its score breakdown", async () => {
    const user = userEvent.setup();
    renderRecs();
    const celeste = await screen.findByRole("link", { name: "Celeste" });
    // The <details> summary contains the game link; open it.
    const summary = celeste.closest("summary")!;
    await user.click(summary);
    expect(await screen.findByText("momentum")).toBeInTheDocument();
    expect(screen.getByText("surging")).toBeInTheDocument();
  });
});
