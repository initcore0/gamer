"""DbEventSink — persists RawEvents into catalog / signals / news tables.

This is the concrete :class:`~gamer.sources.runner.EventSink` that closes the M1
loop: source adapters yield normalized :class:`RawEvent`s, the runner batches
them here, and this maps each :class:`EventKind` to idempotent upserts keyed by
the tables' natural keys. Re-ingesting the same upstream item is a no-op.

Event → table mapping:
  GAME          → games (upsert on (platform, platform_app_id))
  PLAYER_COUNT  → signals_samples (metric=players, upsert on (game,metric,ts))
  REVIEW        → signals_samples (metric=review_count)
  NEWS          → news_items (upsert on (source, external_id))
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
        self._platform = platform

    async def persist(self, events: Sequence[RawEvent]) -> int:
        written = 0
        async with session_scope() as session:
            # Cache appid -> game_id within the batch to avoid repeat lookups.
            game_ids: dict[int, int] = {}

            async def resolve_game(app_id: int, name: str | None = None) -> int:
                if app_id in game_ids:
                    return game_ids[app_id]
                existing = (
                    await session.execute(
                        select(Game.id).where(
                            Game.platform == self._platform,
                            Game.platform_app_id == app_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    game_ids[app_id] = existing
                    return existing
                stub = Game(
                    platform=self._platform,
                    platform_app_id=app_id,
                    name=name or f"app {app_id}",
                )
                session.add(stub)
                await session.flush()
                game_ids[app_id] = stub.id
                return stub.id

            for event in events:
                if event.kind is EventKind.GAME:
                    written += await self._persist_game(session, event, resolve_game, game_ids)
                elif event.kind in (EventKind.PLAYER_COUNT, EventKind.REVIEW):
                    written += await self._persist_sample(session, event, resolve_game)
                elif event.kind is EventKind.NEWS:
                    written += await self._persist_news(session, event, resolve_game)
                # PRICE / RELEASE / TWITCH: folded into GAME/signals later (M4).
        return written

    async def _persist_game(
        self, session: Any, event: RawEvent, resolve_game: Any, game_ids: dict[int, int]
    ) -> int:
        app_id = event.platform_app_id
        if app_id is None:
            return 0
        p = event.payload
        values: dict[str, Any] = {
            "platform": self._platform,
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
        game_ids[app_id] = game_id
        return 1

    async def _persist_sample(self, session: Any, event: RawEvent, resolve_game: Any) -> int:
        app_id = event.platform_app_id
        if app_id is None:
            return 0
        if event.kind is EventKind.PLAYER_COUNT:
            metric = SignalMetric.PLAYERS
            value = event.payload.get("players")
        else:  # REVIEW
            metric = SignalMetric.REVIEW_COUNT
            value = event.payload.get("review_count")
        if value is None:
            return 0
        game_id = await resolve_game(app_id)
        stmt = (
            insert(SignalSample)
            .values(game_id=game_id, metric=metric, ts=event.occurred_at, value=float(value))
            .on_conflict_do_nothing(constraint="uq_sample")
        )
        result = await session.execute(stmt)
        return int(result.rowcount or 0)

    async def _persist_news(self, session: Any, event: RawEvent, resolve_game: Any) -> int:
        app_id = event.platform_app_id
        game_id = await resolve_game(app_id) if app_id is not None else None
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
