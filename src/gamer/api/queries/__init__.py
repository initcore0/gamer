"""Data layer for the web UI (UI_PLAN.md §4).

Plain async functions returning dataclasses / TypedDicts. Every route handler
calls exactly one of these, and each also backs a ``/api/v1`` JSON twin — so
route handlers contain no SQL. Query functions are the integration-test surface;
their pure shaping helpers are unit-tested DB-free.
"""

from __future__ import annotations
