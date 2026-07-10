"""Resilient component discovery.

Score components live in :mod:`gamer.scoring.components` and are authored by
separate agents in parallel (``signals.py`` → momentum/hype/freshness;
``fit.py`` → fit). To let the scorer service work before every component module
lands, discovery is *lazy and defensive*: each known provider is imported inside
a ``try/except`` and any failure (missing module, missing symbol, construction
error) is logged and skipped rather than raised.

Add a new provider by appending a :class:`_Provider` to :data:`_PROVIDERS`. Each
provider names the module, the attribute to pull, and how to turn it into a list
of :class:`~gamer.scoring.base.ScoreComponent` instances.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gamer.logging import get_logger
from gamer.scoring.base import ScoreComponent

log = get_logger("scoring.registry")


@dataclass(frozen=True, slots=True)
class _Provider:
    """One import site: pull ``attr`` from ``module`` and build components."""

    module: str
    attr: str
    build: Callable[[Any], list[ScoreComponent]]


def _instantiate_each(factory: Any) -> list[ScoreComponent]:
    """Treat ``factory`` as a class or a 0-arg callable returning one component."""
    return [factory()]


def _call_returns_list(factory: Any) -> list[ScoreComponent]:
    """Treat ``factory`` as a callable returning a list of components."""
    result = factory()
    return list(result)


# Known component providers. Modules are owned by other agents and may be absent;
# discovery skips whatever fails to import or build.
_SIGNALS = "gamer.scoring.components.signals"
_FIT = "gamer.scoring.components.fit"
_WATCH = "gamer.scoring.components.watchability"
_GENRE_SUB = "gamer.scoring.components.genre_sub"

_PROVIDERS: tuple[_Provider, ...] = (
    # signals.py is expected to expose a builder returning momentum/hype/freshness.
    _Provider(module=_SIGNALS, attr="build_components", build=_call_returns_list),
    _Provider(module=_SIGNALS, attr="MomentumComponent", build=_instantiate_each),
    _Provider(module=_SIGNALS, attr="HypeComponent", build=_instantiate_each),
    _Provider(module=_SIGNALS, attr="FreshnessComponent", build=_instantiate_each),
    # watchability.py (Twitch viewers-to-players) — M4.
    _Provider(module=_WATCH, attr="WatchabilityComponent", build=_instantiate_each),
    # fit.py is expected to expose FitComponent.
    _Provider(module=_FIT, attr="FitComponent", build=_instantiate_each),
    # genre_sub.py (subscribed-genre hard boost) — M7.
    _Provider(module=_GENRE_SUB, attr="GenreSubComponent", build=_instantiate_each),
)


def discover_components() -> list[ScoreComponent]:
    """Import and instantiate every available component, skipping failures.

    Returns a de-duplicated (by ``key``) list. A ``build_components`` batch
    factory, when present, takes precedence over individual class providers for
    the same keys. Logs which component keys loaded and which providers failed.
    """
    by_key: dict[str, ScoreComponent] = {}
    for provider in _PROVIDERS:
        try:
            module = importlib.import_module(provider.module)
        except ImportError as exc:
            log.debug("component_module_absent", module=provider.module, error=str(exc))
            continue
        factory = getattr(module, provider.attr, None)
        if factory is None:
            log.debug("component_attr_absent", module=provider.module, attr=provider.attr)
            continue
        try:
            components = provider.build(factory)
        except Exception as exc:  # one bad provider must not break discovery
            log.warning(
                "component_build_failed",
                module=provider.module,
                attr=provider.attr,
                error=str(exc),
            )
            continue
        for component in components:
            key = getattr(component, "key", None)
            if not isinstance(key, str):
                log.warning("component_missing_key", module=provider.module, attr=provider.attr)
                continue
            if key not in by_key:
                by_key[key] = component

    loaded = sorted(by_key)
    log.info("components_discovered", keys=loaded, count=len(loaded))
    return [by_key[k] for k in loaded]
