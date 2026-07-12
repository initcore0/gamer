import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { server } from "./src/test/server";

// jsdom has no matchMedia; the theme hook reads it. Use a plain function (NOT a
// vi.fn) so `restoreMocks` between tests can't reset it to return undefined.
window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

// jsdom lacks scrollTo (used by pages that reset scroll on navigation).
window.scrollTo = window.scrollTo ?? vi.fn();

// jsdom has no canvas rendering; uPlot calls getContext("2d"). Return a minimal
// stub so the chart mounts (we assert it renders, not its pixels).
const ctxStub = new Proxy(
  {},
  {
    get: (_t, prop) => {
      if (prop === "canvas") return document.createElement("canvas");
      if (prop === "measureText") return () => ({ width: 0 });
      if (prop === "getImageData")
        return () => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 });
      return () => {};
    },
  },
);
HTMLCanvasElement.prototype.getContext = (() =>
  ctxStub) as unknown as HTMLCanvasElement["getContext"];

// uPlot builds Path2D objects for its strokes; jsdom has no Path2D. A no-op
// class is enough for the chart to mount without throwing during commit.
if (typeof (globalThis as { Path2D?: unknown }).Path2D === "undefined") {
  class Path2DStub {
    addPath() {}
    moveTo() {}
    lineTo() {}
    arc() {}
    rect() {}
    closePath() {}
  }
  (globalThis as { Path2D?: unknown }).Path2D = Path2DStub;
}

// IntersectionObserver drives infinite scroll; jsdom has no implementation.
class MockIntersectionObserver implements IntersectionObserver {
  readonly root: Element | null = null;
  readonly rootMargin: string = "";
  readonly thresholds: ReadonlyArray<number> = [];
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  takeRecords = vi.fn(() => []);
}
window.IntersectionObserver =
  window.IntersectionObserver ?? (MockIntersectionObserver as unknown as typeof IntersectionObserver);

// ResizeObserver drives the fluid-width chart; jsdom has no implementation.
class MockResizeObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
window.ResizeObserver =
  window.ResizeObserver ?? (MockResizeObserver as unknown as typeof ResizeObserver);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
