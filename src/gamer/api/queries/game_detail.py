"""Game-detail query layer (UI_PLAN.md §3.3, §8 UI-M3).

Everything the ``/games/{id}`` page needs, as plain async functions returning
dataclasses (the JSON twin shares them). A new module rather than bloating
``queries/games.py``: the catalog list and the single-game detail have no shared
SQL, and keeping detail here mirrors the plan's "one file per concern" layout.

Functions:
  * :func:`game_detail`     — game columns + the precomputed ``game_stats`` row.
  * :func:`latest_breakdown`— newest recommendation's score + breakdown jsonb.
  * :func:`game_news`       — news for the game, newest first, grouped by cluster.
  * :func:`similar_games`   — pgvector cosine nearest neighbours over embeddings.

Rules (UI_PLAN.md §9): no SQL in routes; every value a bound param; the
similar-games vector comparison uses pgvector's ``<=>`` operator over the HNSW
index (migration 0004). DB round-trips → integration-marked; the cluster-grouping
shaping (:func:`group_news_by_cluster`) is a pure, unit-tested helper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from gamer.db import session_scope
from gamer.db.models import Game, GameStats, NewsItem, Recommendation


@dataclass(frozen=True, slots=True)
class GameDetail:
    """Header + precomputed stats for one game (UI_PLAN.md §3.3 header + charts)."""

    id: int
    name: str
    platform: str
    platform_app_id: int
    genres: list[str]
    release_date: datetime | None
    price_cents: int | None
    is_free: bool
    tracked: bool
    current_players: float | None = None
    players_24h_delta: float | None = None
    review_count: float | None = None
    twitch_viewers: float | None = None
    last_signal_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Breakdown:
    """The latest recommendation's score + component/penalty breakdown jsonb."""

    score: float
    breakdown: dict[str, Any]
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewsCard:
    """One cluster-deduped news card: a primary item + count of similar others."""

    id: int
    title: str
    url: str | None
    source: str
    published_at: datetime
    cluster_id: int | None
    similar_count: int
    similar: list[NewsRef] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class NewsRef:
    """A secondary story folded under a cluster's primary card.

    ``cluster_id`` drives the grouping in :func:`group_news_by_cluster`; it is not
    rendered (the card's ``cluster_id`` is used instead).
    """

    id: int
    title: str
    url: str | None
    source: str
    published_at: datetime
    cluster_id: int | None = None


@dataclass(frozen=True, slots=True)
class SimilarGame:
    """A nearest-neighbour game for the similar-games chips."""

    id: int
    name: str
    platform: str
    distance: float


async def game_detail(game_id: int) -> GameDetail | None:
    """Game columns LEFT JOINed with its ``game_stats`` row; ``None`` if unknown."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(
                    Game.id,
                    Game.name,
                    Game.platform,
                    Game.platform_app_id,
                    Game.genres,
                    Game.release_date,
                    Game.price_cents,
                    Game.is_free,
                    Game.tracked,
                    GameStats.current_players,
                    GameStats.players_24h_delta,
                    GameStats.review_count,
                    GameStats.twitch_viewers,
                    GameStats.last_signal_at,
                )
                .select_from(Game)
                .outerjoin(GameStats, GameStats.game_id == Game.id)
                .where(Game.id == game_id)
            )
        ).first()
    if row is None:
        return None
    (
        gid,
        name,
        platform,
        platform_app_id,
        genres,
        release_date,
        price_cents,
        is_free,
        tracked,
        current_players,
        players_24h_delta,
        review_count,
        twitch_viewers,
        last_signal_at,
    ) = row
    return GameDetail(
        id=int(gid),
        name=str(name),
        platform=platform.value if hasattr(platform, "value") else str(platform),
        platform_app_id=int(platform_app_id),
        genres=list(genres or []),
        release_date=release_date,
        price_cents=None if price_cents is None else int(price_cents),
        is_free=bool(is_free),
        tracked=bool(tracked),
        current_players=None if current_players is None else float(current_players),
        players_24h_delta=None if players_24h_delta is None else float(players_24h_delta),
        review_count=None if review_count is None else float(review_count),
        twitch_viewers=None if twitch_viewers is None else float(twitch_viewers),
        last_signal_at=last_signal_at,
    )


async def latest_breakdown(game_id: int) -> Breakdown | None:
    """Newest recommendation's score + breakdown for the game; ``None`` if none."""
    async with session_scope() as session:
        row = (
            await session.execute(
                select(
                    Recommendation.score,
                    Recommendation.breakdown,
                    Recommendation.created_at,
                )
                .where(Recommendation.game_id == game_id)
                .order_by(Recommendation.created_at.desc())
                .limit(1)
            )
        ).first()
    if row is None:
        return None
    score, breakdown, created_at = row
    return Breakdown(
        score=float(score),
        breakdown=dict(breakdown or {}),
        created_at=created_at,
    )


def group_news_by_cluster(rows: list[NewsRef], *, limit: int) -> list[NewsCard]:
    """Fold news rows (newest first) into ≤``limit`` cluster-deduped cards (pure).

    One card per ``cluster_id`` — the first (newest) row is the primary, later
    rows in the same cluster become ``similar`` refs and bump ``similar_count``.
    Rows with ``cluster_id is None`` are each their own card (unclustered). Input
    order is preserved; at most ``limit`` cards are returned.
    """
    cards: list[NewsCard] = []
    index: dict[int, int] = {}  # cluster_id -> position in `cards`
    for r in rows:
        cid = r.cluster_id
        if cid is not None and cid in index:
            card = cards[index[cid]]
            cards[index[cid]] = NewsCard(
                id=card.id,
                title=card.title,
                url=card.url,
                source=card.source,
                published_at=card.published_at,
                cluster_id=card.cluster_id,
                similar_count=card.similar_count + 1,
                similar=[
                    *card.similar,
                    NewsRef(r.id, r.title, r.url, r.source, r.published_at),
                ],
            )
            continue
        if len(cards) >= limit:
            continue
        if cid is not None:
            index[cid] = len(cards)
        cards.append(
            NewsCard(
                id=r.id,
                title=r.title,
                url=r.url,
                source=r.source,
                published_at=r.published_at,
                cluster_id=cid,
                similar_count=0,
                similar=[],
            )
        )
    return cards


async def game_news(game_id: int, *, limit: int = 20) -> list[NewsCard]:
    """News for the game, newest first, grouped by cluster (UI_PLAN.md §3.3).

    Fetches ``limit * 2`` rows (a game's clusters are small) and groups in Python
    via :func:`group_news_by_cluster` into ≤``limit`` cards. Talks to the DB →
    integration-only.
    """
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(
                    NewsItem.id,
                    NewsItem.title,
                    NewsItem.url,
                    NewsItem.source,
                    NewsItem.published_at,
                    NewsItem.cluster_id,
                )
                .where(NewsItem.game_id == game_id)
                .order_by(NewsItem.published_at.desc())
                .limit(limit * 2)
            )
        ).all()

    refs = [
        NewsRef(
            id=int(nid),
            title=str(title),
            url=url,
            source=str(source),
            published_at=published_at,
            cluster_id=None if cluster_id is None else int(cluster_id),
        )
        for nid, title, url, source, published_at, cluster_id in rows
    ]
    return group_news_by_cluster(refs, limit=limit)


async def similar_games(game_id: int, *, limit: int = 6) -> list[SimilarGame]:
    """pgvector cosine nearest neighbours of the game (UI_PLAN.md §3.3).

    Reads the target's stored ``embedding`` (backfilled by ``stats:refresh``) and
    scans the HNSW index for the nearest other games with a non-null embedding,
    excluding self. Returns ``[]`` when the target has no embedding yet. Talks to
    the DB → integration-only.
    """
    async with session_scope() as session:
        target = await session.scalar(select(Game.embedding).where(Game.id == game_id))
        if target is None:
            return []
        distance = Game.embedding.cosine_distance(target).label("distance")
        rows = (
            await session.execute(
                select(Game.id, Game.name, Game.platform, distance)
                .where(Game.id != game_id)
                .where(Game.embedding.is_not(None))
                .order_by(distance)
                .limit(limit)
            )
        ).all()
    return [
        SimilarGame(
            id=int(gid),
            name=str(name),
            platform=platform.value if hasattr(platform, "value") else str(platform),
            distance=float(dist),
        )
        for gid, name, platform, dist in rows
    ]
