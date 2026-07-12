import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Layout } from "../components/Layout";

function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Render a single element under a fresh QueryClient + router at `route`. */
export function renderWithProviders(ui: ReactElement, route = "/") {
  const client = testQueryClient();
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

/**
 * Render the app's real route tree (Layout + one page) so URL-state and links
 * behave. Pass a `path`/`element` pair plus the entry `route`.
 */
export function renderRoute(
  path: string,
  element: ReactNode,
  route: string,
  extra?: { path: string; element: ReactNode }[],
) {
  const client = testQueryClient();
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route element={<Layout />}>
            <Route path={path} element={element} />
            {extra?.map((r) => (
              <Route key={r.path} path={r.path} element={r.element} />
            ))}
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}
