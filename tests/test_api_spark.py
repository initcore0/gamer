"""Unit tests for the server-rendered SVG sparkline helper (UI_PLAN.md §5).

Pure function. Verifies coordinate scaling, degenerate inputs (empty / 1-point /
constant), and — the security-critical property — that the output contains no
characters outside the SVG alphabet, so it stays safe to render via ``|safe``.
"""

from __future__ import annotations

import re

from gamer.api.spark import SVG_ALPHABET, spark_svg


def test_empty_is_empty_string() -> None:
    assert spark_svg([]) == ""


def test_single_point_is_empty_string() -> None:
    assert spark_svg([5.0]) == ""


def test_two_points_render_polyline() -> None:
    out = spark_svg([0.0, 10.0], width=100, height=20)
    assert out.startswith("<svg")
    assert "<polyline" in out
    assert 'stroke="currentColor"' in out


def test_scaling_endpoints() -> None:
    # Ascending 0→10 over width 100, height 20: x spans 0..100; y inverts so the
    # min value sits at the bottom (y=20) and the max at the top (y=0).
    out = spark_svg([0.0, 10.0], width=100, height=20)
    m = re.search(r'points="([^"]+)"', out)
    assert m is not None
    coords = [tuple(map(float, p.split(","))) for p in m.group(1).split()]
    assert coords[0] == (0.0, 20.0)
    assert coords[-1] == (100.0, 0.0)


def test_constant_series_is_flat_midline() -> None:
    out = spark_svg([7.0, 7.0, 7.0], width=100, height=20)
    m = re.search(r'points="([^"]+)"', out)
    assert m is not None
    ys = [float(p.split(",")[1]) for p in m.group(1).split()]
    assert set(ys) == {10.0}  # height / 2


def test_output_uses_only_svg_alphabet() -> None:
    # A varied series with fractional coordinates.
    out = spark_svg([1.0, 3.5, 2.0, 9.0, 4.25, 0.0, 100.0])
    leftover = set(out) - SVG_ALPHABET
    assert leftover == set(), f"unexpected chars: {leftover!r}"


def test_no_injection_even_with_huge_values() -> None:
    # Values are floats; nothing but digits/./-/e/E/space can appear in coords.
    out = spark_svg([-1e9, 1e9])
    m = re.search(r'points="([^"]+)"', out)
    assert m is not None
    assert all(c in "0123456789.-eE ," for c in m.group(1))
