"""HTTP routers for the web UI (UI_PLAN.md §4).

One module per section. Each route calls exactly one ``queries/`` function and
renders a template (or JSON twin) — no SQL in handlers.
"""

from __future__ import annotations
