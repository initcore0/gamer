"""Server-rendered SVG sparklines for catalog rows (UI_PLAN.md §5, UI-M2).

A single pure helper, :func:`spark_svg`, turns a list of floats into a minimal
``<svg><polyline/></svg>``. It is the *sole* template output piped through
``|safe`` (autoescape is otherwise on everywhere — §9 review gate), so it is
built **exclusively from floats**: every coordinate is coerced through ``float``
and formatted with ``%g``, and the stroke colour is the literal ``currentColor``.
No caller string ever reaches the markup, making it injection-proof by
construction. Unit-tested for scaling, degenerate inputs, and that the output
contains no characters outside the SVG alphabet.
"""

from __future__ import annotations

# The complete set of characters the generated markup may contain — asserted in
# tests so a future edit that lets a raw string leak in is caught immediately.
# Digits + float syntax for coordinates, plus every letter/symbol used in the
# fixed markup below (element/attribute names and their static values).
_MARKUP_CHARS = (
    '<svg class="spark" viewBox="0 0 " width="" height="" '
    'preserveAspectRatio="none"><polyline fill="none" stroke="currentColor" '
    'stroke-width="1" points=""/></svg>'
)
SVG_ALPHABET = set("0123456789.-eE +,") | set(_MARKUP_CHARS)


def spark_svg(points: list[float], width: int = 120, height: int = 28) -> str:
    """Render ``points`` as a minimal polyline sparkline SVG string.

    * ``< 2`` points → ``""`` (nothing meaningful to draw).
    * Constant series (min == max) → a flat line down the vertical middle.
    * Otherwise the values are scaled to ``[0, height]`` (inverted so larger
      values sit higher) across the full ``width``.

    Stroke is ``currentColor`` so the line inherits the row's text colour. Every
    numeric token is a coerced ``float`` formatted with ``%g`` — no user string
    is ever interpolated, so the result is safe to mark ``|safe``.
    """
    if len(points) < 2:
        return ""

    vals = [float(p) for p in points]
    w = float(width)
    h = float(height)
    lo = min(vals)
    hi = max(vals)
    span = hi - lo

    n = len(vals)
    step = w / (n - 1)
    coords: list[str] = []
    for i, v in enumerate(vals):
        x = i * step
        if span == 0.0:
            y = h / 2.0
        else:
            # Invert: bigger value → smaller y (higher on screen).
            y = h - ((v - lo) / span) * h
        coords.append(f"{x:g},{y:g}")

    pts = " ".join(coords)
    return (
        f'<svg class="spark" viewBox="0 0 {w:g} {h:g}" '
        f'width="{w:g}" height="{h:g}" preserveAspectRatio="none">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="1" '
        f'points="{pts}"/></svg>'
    )
