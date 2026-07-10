"""Shared Jinja2 environment for the web UI (UI_PLAN.md §4).

One :class:`Jinja2Templates` instance, resolved relative to this package so
``importlib``/installed-wheel layouts find ``templates/`` via
``Path(__file__).parent``. Autoescape is on by default (Jinja2Templates enables
it for ``.html``) — the §9 review gate. Routers import ``templates`` from here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
