import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { DashboardPage } from "./DashboardPage";
import { renderRoute } from "../test/renderApp";

describe("DashboardPage", () => {
  it("puts sync freshness front and center with per-source state", async () => {
    renderRoute("", <DashboardPage />, "/");

    // Freshness section renders first, with the stale banner.
    expect(await screen.findByText("Sync freshness")).toBeInTheDocument();
    expect(screen.getByText(/1 source stale/i)).toBeInTheDocument();

    // The rss source (last_success_at null) is badged STALE.
    const rss = screen.getByText("rss").closest(".freshness") as HTMLElement;
    expect(within(rss).getByText("STALE")).toBeInTheDocument();
  });

  it("shows status counts, top movers, latest recs and digest times", async () => {
    renderRoute("", <DashboardPage />, "/");
    await screen.findByText("Sync freshness");

    // Counts tile.
    expect(screen.getByText("320")).toBeInTheDocument();

    // Top movers strip + latest-recs strip both link Celeste to game detail.
    const celesteLinks = screen.getAllByRole("link", { name: /Celeste/ });
    expect(celesteLinks.length).toBeGreaterThanOrEqual(1);
    expect(celesteLinks.every((l) => l.getAttribute("href") === "/games/1")).toBe(true);

    // Digest section shows last + next.
    expect(screen.getByText(/telegram_group/)).toBeInTheDocument();
    expect(screen.getByText(/Next digest:/)).toBeInTheDocument();
  });
});
