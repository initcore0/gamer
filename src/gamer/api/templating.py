"""Shared Jinja2 environment for the web UI (UI_PLAN.md §4).

One :class:`Jinja2Templates` instance, resolved relative to this package so
``importlib``/installed-wheel layouts find ``templates/`` via
``Path(__file__).parent``. Autoescape is on by default (Jinja2Templates enables
it for ``.html``) — the §9 review gate. Routers import ``templates`` from here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from gamer.api.spark import spark_svg

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _spark_filter(points: list[float]) -> Markup:
    """Jinja filter: render a float list as a safe inline SVG sparkline.

    The sole place UI output is trusted as HTML — :func:`spark_svg` builds its
    markup exclusively from coerced floats and fixed tokens (see its docstring),
    so wrapping in :class:`~markupsafe.Markup` is provably injection-safe.
    """
    return Markup(spark_svg(list(points)))


templates.env.filters["spark"] = _spark_filter
