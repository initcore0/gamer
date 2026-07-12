import { NavLink, Link, Outlet, useLocation } from "react-router-dom";
import { useTheme } from "../lib/useTheme";
import { ErrorBoundary } from "./ErrorBoundary";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/games", label: "Games", end: false },
  { to: "/recommendations", label: "Recommendations", end: false },
  { to: "/news", label: "News", end: false },
  { to: "/sources", label: "Sources", end: false },
];

export function Layout() {
  const { theme, toggle } = useTheme();
  const location = useLocation();
  return (
    <div className="app">
      <div className="shell">
        <header className="site-header">
          <Link to="/" className="brand">
            gamer
          </Link>
          <nav className="site-nav" aria-label="Primary">
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end}>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="header-spacer" />
          <button
            type="button"
            className="theme-toggle"
            onClick={toggle}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            title="Toggle theme"
          >
            {theme === "dark" ? "☾" : "☀"}
          </button>
        </header>
        <main>
          {/* Keyed by path so a route change resets a tripped ErrorBoundary. */}
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
