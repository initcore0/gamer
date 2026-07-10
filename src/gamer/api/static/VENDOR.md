# Vendored static assets

All third-party front-end assets are self-hosted here — the public UI makes **no
external requests** (no CDN scripts, fonts, or styles). See UI_PLAN.md §2 (row
"Static assets") and §9 (CSP `default-src 'self'`). To refresh, re-download from
the URLs below and update the version + date.

| File | Version | Source URL | Downloaded |
|---|---|---|---|
| `htmx.min.js` | 2.0.10 | https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js | 2026-07-09 |
| `uPlot.iife.min.js` | 1.6.32 | https://unpkg.com/uplot@1.6.32/dist/uPlot.iife.min.js | 2026-07-09 |
| `uPlot.min.css` | 1.6.32 | https://unpkg.com/uplot@1.6.32/dist/uPlot.min.css | 2026-07-09 |

`app.css` is hand-authored (not vendored) — see that file.
