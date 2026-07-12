import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { SourcesPage } from "./SourcesPage";
import { renderRoute } from "../test/renderApp";

describe("SourcesPage", () => {
  it("renders staleness, job history, and events-per-day", async () => {
    renderRoute("sources", <SourcesPage />, "/sources");

    // The stale rss card badges STALE; the healthy steam_api card does not.
    const rssHead = (await screen.findByText("rss")).closest(".source-card") as HTMLElement;
    expect(within(rssHead).getByText("STALE")).toBeInTheDocument();

    const steamHead = screen.getByText("steam_api").closest(".source-card") as HTMLElement;
    expect(within(steamHead).queryByText("STALE")).not.toBeInTheDocument();

    // The redacted/truncated job error surfaces.
    expect(screen.getByText("HTTPError: boom")).toBeInTheDocument();

    // Events-per-day bar chart is present.
    expect(
      screen.getByRole("img", { name: /samples per day/i }),
    ).toBeInTheDocument();
  });
});
