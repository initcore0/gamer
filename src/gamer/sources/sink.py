"""DbEventSink — persists RawEvents into catalog / signals / news tables.

This is the concrete :class:`~gamer.sources.runner.EventSink` that closes the M1
loop: source adapters yield normalized :class:`RawEvent`s, the runner batches
them here, and this maps each :class:`EventKind` to idempotent upserts keyed by
the tables' natural keys. Re-ingesting the same upstream item is a no-op.

Event → table mapping:
  GAME / RELEASE → games (upsert on (platform, platform_app_id))
  PLAYER_COUNT   → signals_samples (metric=players, upsert on (game,metric,ts))
  REVIEW         → signals_samples (metric=review_count)
  TWITCH         → signals_samples (metric=twitch_viewers); skipped if unmappable
  NEWS           → news_items (upsert on (source, external_id))
Each event's platform is resolved from ``RawEvent.platform`` (falling back to the
sink default), so one sink can persist multiple platforms (PLAN.md §5 M5).
Events for an unknown appid create a stub Game row so samples/news always link.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from gamer.db import session_scope
from gamer.db.models import Game, NewsItem, Platform, SignalMetric, SignalSample
from gamer.logging import get_logger
from gamer.sources.base import EventKind, RawEvent

log = get_logger("sources.sink")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


class DbEventSink:
    """Idempotent persistence of RawEvents. Safe to call repeatedly."""

    def __init__(self, *, platform: Platform = Platform.STEAM) -> None:
        # Default platform for events that don't declare one (Steam sources).
        self._platform = platform

    def _event_platform(self, event: RawEvent) -> Platform:
        """Resolve an event's platform, falling back to the sink default. This is
        the seam that lets one sink persist multiple platforms (PLAN.md §5 M5)."""
        if event.platform is None:
            return self._platform
        return Platform(event.platform)

    async def persist(self, events: Sequence[RawEvent]) -> int:
        written = 0
        async with session_scope() as session:
            # Cache (platform, app_id) -> game_id within the batch. Keyed by
            # platform too, since appids are only unique *within* a platform.
            game_ids: dict[tuple[Platform, int], int] = {}

            async def resolve_game(platform: Platform, app_id: int, name: str | None = None) -> int:
                key = (platform, app_id)
                if key in game_ids:
                    return game_ids[key]
                existing = (
                    await session.execute(
                        select(Game.id).where(
                            Game.platform == platform,
                            Game.platform_app_id == app_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    game_ids[key] = existing
                    return existing
                stub = Game(
                    platform=platform,
                    platform_app_id=app_id,
                    name=name or f"app {app_id}",
                )
                session.add(stub)
                await session.flush()
                game_ids[key] = stub.id
                return stub.id

            for event in events:
                platform = self._event_platform(event)
                if event.kind in (EventKind.GAME, EventKind.RELEASE):
                    written += await self._persist_game(
                        session, event, platform, resolve_game, game_ids
                    )
                elif event.kind in (EventKind.PLAYER_COUNT, EventKind.REVIEW, EventKind.TWITCH):
                    written += await self._persist_sample(session, event, platform, resolve_game)
                elif event.kind is EventKind.NEWS:
                    written += await self._persist_news(session, event, platform, resolve_game)
                # PRICE: folded into GAME/signals later.
        return written

    async def _persist_game(
        self,
        session: Any,
        event: RawEvent,
        platform: Platform,
        resolve_game: Any,
        game_ids: dict[tuple[Platform, int], int],
    ) -> int:
        app_id = event.platform_app_id
        if app_id is None:
            return 0
        p = event.payload
        values: dict[str, Any] = {
            "platform": platform,
            "platform_app_id": app_id,
            "name": p.get("name") or f"app {app_id}",
        }
        # Only overwrite optional details when the event actually carries them
        # (the catalog-sync GAME event only knows the name).
        update_cols: dict[str, Any] = {"name": values["name"], "updated_at": datetime.now(UTC)}
        if "genres" in p:
            values["genres"] = p["genres"] or []
            update_cols["genres"] = values["genres"]
        if "price_cents" in p:
            values["price_cents"] = p["price_cents"]
            update_cols["price_cents"] = p["price_cents"]
        if "is_free" in p:
            values["is_free"] = bool(p["is_free"])
            update_cols["is_free"] = values["is_free"]
        if "release_date" in p:
            values["release_date"] = _parse_dt(p["release_date"])
            update_cols["release_date"] = values["release_date"]
        if any(k in p for k in ("genres", "price_cents", "is_free", "release_date")):
            update_cols["details_fetched_at"] = datetime.now(UTC)

        stmt = (
            insert(Game)
            .values(**values)
            .on_conflict_do_update(constraint="uq_game_platform_app", set_=update_cols)
            .returning(Game.id)
        )
        game_id = (await session.execute(stmt)).scalar_one()
        game_ids[(platform, app_id)] = game_id
        return 1

    async def _persist_sample(
        self, session: Any, event: RawEvent, platform: Platform, resolve_game: Any
    ) -> int:
        app_id = event.platform_app_id
        if app_id is None:
            return 0
        if event.kind is EventKind.PLAYER_COUNT:
            metric = SignalMetric.PLAYERS
            value = event.payload.get("players")
        elif event.kind is EventKind.TWITCH:
            metric = SignalMetric.TWITCH_VIEWERS
            value = event.payload.get("viewers")
        else:  # REVIEW
            metric = SignalMetric.REVIEW_COUNT
            value = event.payload.get("review_count")
        if value is None:
            return 0
        game_id = await resolve_game(platform, app_id)
        stmt = (
            insert(SignalSample)
            .values(game_id=game_id, metric=metric, ts=event.occurred_at, value=float(value))
            .on_conflict_do_nothing(constraint="uq_sample")
        )
        result = await session.execute(stmt)
        return int(result.rowcount or 0)

    async def _persist_news(
        self, session: Any, event: RawEvent, platform: Platform, resolve_game: Any
    ) -> int:
        app_id = event.platform_app_id
        game_id = await resolve_game(platform, app_id) if app_id is not None else None
        p = event.payload
        published = _parse_dt(p.get("published_at")) or event.occurred_at
        stmt = (
            insert(NewsItem)
            .values(
                source=event.source,
                external_id=event.natural_key,
                game_id=game_id,
                title=p.get("title") or "",
                url=p.get("url"),
                body=p.get("body"),
                published_at=published,
            )
            .on_conflict_do_nothing(constraint="uq_news_natural_key")
        )
        result = await session.execute(stmt)
        return int(result.rowcount or 0)
