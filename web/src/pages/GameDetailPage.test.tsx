import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GameDetailPage } from "./GameDetailPage";
import { renderRoute } from "../test/renderApp";

function renderDetail(route: string) {
  return renderRoute("games/:id", <GameDetailPage />, route);
}

describe("GameDetailPage", () => {
  it("renders header stats, score bars, news and similar games", async () => {
    renderDetail("/games/1");
    expect(await screen.findByRole("heading", { name: "Celeste" })).toBeInTheDocument();
    // Score breakdown bar + penalty.
    expect(screen.getByText("momentum")).toBeInTheDocument();
    expect(screen.getByText(/recently streamed/)).toBeInTheDocument();
    // News + similar sections.
    expect(screen.getByText(/Celeste gets a surprise update/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Hades" })).toBeInTheDocument();
    // Steam store deep link.
    expect(screen.getByRole("link", { name: /Steam store/ })).toHaveAttribute(
      "href",
      "https://store.steampowered.com/app/504230",
    );
  });

  it("switches the chart metric without crashing", async () => {
    const user = userEvent.setup();
    const { container } = renderDetail("/games/1");
    await screen.findByRole("heading", { name: "Celeste" });
    // The chart container mounts (uPlot draws into it) once the series loads.
    await waitFor(() => expect(container.querySelector(".chart")).toBeInTheDocument());
    // Metric switcher toggles the active chip.
    await user.click(screen.getByRole("button", { name: "Reviews" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Reviews" })).toHaveClass("active"),
    );
  });

  it("shows a not-found state for an unknown game", async () => {
    renderDetail("/games/999");
    expect(await screen.findByRole("heading", { name: /game not found/i })).toBeInTheDocument();
  });
});
