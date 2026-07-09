"""Source adapters. Register new sources in :data:`REGISTRY`.

Each milestone-1 agent adds one module here (steam_api, steam_store, steamspy,
rss, twitch) implementing the :class:`~gamer.sources.base.Source` protocol, then
appends its factory to the registry below.
"""

from __future__ import annotations

from collections.abc import Callable

from gamer.sources.base import (
    EventKind,
    FetchContext,
    FetchResult,
    RawEvent,
    Source,
)

__all__ = [
    "REGISTRY",
    "EventKind",
    "FetchContext",
    "FetchResult",
    "RawEvent",
    "Source",
    "register",
]

#: name -> zero-arg factory that builds a configured Source instance.
REGISTRY: dict[str, Callable[[], Source]] = {}


def register(name: str) -> Callable[[Callable[[], Source]], Callable[[], Source]]:
    """Decorator to register a source factory.

    Usage::

        @register("steam_api")
        def _build() -> Source:
            return SteamApiSource(...)
    """

    def _wrap(factory: Callable[[], Source]) -> Callable[[], Source]:
        if name in REGISTRY:
            raise ValueError(f"source already registered: {name}")
        REGISTRY[name] = factory
        return factory

    return _wrap


# Source factories are registered here (not via a decorator inside each adapter
# module) so importing this package populates REGISTRY without importing every
# adapter's heavy deps. Each factory lazily imports its module on first build.


@register("steam_api")
def _build_steam_api() -> Source:
    from gamer.sources.steam_api import SteamApiSource

    return SteamApiSource()


@register("steam_store")
def _build_steam_store() -> Source:
    from gamer.sources.steam_store import SteamStoreSource

    return SteamStoreSource()


@register("twitch")
def _build_twitch() -> Source:
    from gamer.sources.twitch import TwitchSource

    return TwitchSource()


@register("rss")
def _build_rss() -> Source:
    from gamer.sources.rss import RssSource

    return RssSource()
