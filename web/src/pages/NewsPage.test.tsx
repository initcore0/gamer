import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NewsPage } from "./NewsPage";
import { renderRoute } from "../test/renderApp";

function renderNews(route = "/news") {
  return renderRoute("news", <NewsPage />, route);
}

describe("NewsPage", () => {
  it("renders cluster-grouped cards with a similar-count badge", async () => {
    renderNews();
    expect(await screen.findByText("Big Patch Lands")).toBeInTheDocument();
    expect(screen.getByText("+1 similar")).toBeInTheDocument();
    // Expanding the cluster reveals the folded story.
    const details = screen.getByText(/1 related stories/i);
    expect(details).toBeInTheDocument();
  });

  it("appends the next page via Load more", async () => {
    const user = userEvent.setup();
    renderNews();
    await screen.findByText("Big Patch Lands");
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Older headline")).toBeInTheDocument();
    expect(screen.getByText("Big Patch Lands")).toBeInTheDocument();
  });

  it("filters by source (URL state) and refetches", async () => {
    const user = userEvent.setup();
    renderNews();
    await screen.findByText("Big Patch Lands");
    await user.selectOptions(screen.getByLabelText("Source"), "eurogamer");
    // The eurogamer filter returns the page-2 fixture only.
    expect(await screen.findByText("Older headline")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Big Patch Lands")).not.toBeInTheDocument(),
    );
  });
});
