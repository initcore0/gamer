from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from gamer.sources import REGISTRY, register
from gamer.sources.base import (
    EventKind,
    FetchContext,
    RawEvent,
    Source,
)


class _FakeSource:
    name = "fake"
    default_interval_seconds = 60

    async def fetch(self, ctx: FetchContext) -> AsyncIterator[RawEvent]:
        yield RawEvent(
            source=self.name,
            kind=EventKind.NEWS,
            natural_key="gid-1",
            payload={"title": "hi"},
            occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            platform_app_id=440,
        )


def test_fake_source_satisfies_protocol() -> None:
    src: Source = _FakeSource()
    assert isinstance(src, Source)


async def test_fetch_yields_events() -> None:
    events = [e async for e in _FakeSource().fetch(FetchContext())]
    assert len(events) == 1
    assert events[0].dedup_key() == "fake:news:gid-1"
    assert events[0].kind is EventKind.NEWS


def test_registry_registration_and_duplicate_guard() -> None:
    @register("unit-test-src")
    def _factory() -> Source:
        return _FakeSource()

    try:
        assert REGISTRY["unit-test-src"]().name == "fake"
        with pytest.raises(ValueError, match="already registered"):

            @register("unit-test-src")
            def _dupe() -> Source:
                return _FakeSource()
    finally:
        REGISTRY.pop("unit-test-src", None)
