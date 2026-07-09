"""Enrichment jobs — news dedup/clustering (PLAN.md §4.4, M4).

The scheduled :func:`enrich_news_once` finds recently-ingested ``news_items`` whose
``embedding`` is still NULL, embeds each one, and assigns a ``cluster_id`` joining
near-identical stories. This is what stops the digest from repeating the same
announcement reworded across three outlets.

It REUSES the committed helpers in :mod:`gamer.enrichment.embeddings` verbatim:

* :func:`~gamer.enrichment.embeddings.embed_news_item` — computes and persists the
  embedding for one row (via :func:`~gamer.enrichment.embeddings.get_embedder`).
* :func:`~gamer.enrichment.embeddings.cluster_news_item` — finds near-duplicates
  (:func:`~gamer.enrichment.embeddings.find_near_duplicates`) and joins the nearest
  cluster or starts a fresh one.

**Always-on.** :func:`get_embedder` already falls back to the deterministic
:class:`~gamer.enrichment.embeddings.HashEmbedder` when ``embeddings.enabled`` is
False, so the job runs and clusters even without the ML extra or a GPU (the hash
embedder still collapses byte-identical stories). No feature flag is needed.

The DB round-trip (:func:`_recent_unembedded_ids`) is factored out so the pure
decision loop (:func:`_enrich_ids`) is unit-testable with fake ids and injected
helper callables — no database, no model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from gamer.enrichment.embeddings import (
    Embedder,
    cluster_news_item,
    embed_news_item,
    get_embedder,
)
from gamer.logging import get_logger

log = get_logger("enrichment.jobs")

# Type aliases for the injectable helper callables (keeps the loop DB-free in tests).
EmbedFn = Callable[[int, Embedder], Awaitable[list[float]]]
ClusterFn = Callable[[int, list[float]], Awaitable[int]]


async def _recent_unembedded_ids(limit: int) -> list[int]:
    """Ids of the most recent ``news_items`` whose ``embedding`` is still NULL.

    Talks to the database → exercised only under ``@pytest.mark.integration``.
    """
    from sqlalchemy import select

    from gamer.db import session_scope
    from gamer.db.models import NewsItem

    stmt = (
        select(NewsItem.id)
        .where(NewsItem.embedding.is_(None))
        .order_by(NewsItem.published_at.desc())
        .limit(limit)
    )
    async with session_scope() as session:
        rows = await session.execute(stmt)
        return [int(nid) for nid in rows.scalars().all()]


async def _enrich_ids(
    ids: list[int],
    *,
    embedder: Embedder,
    embed: EmbedFn,
    cluster: ClusterFn,
) -> int:
    """Embed then cluster each id in order. Returns the count successfully clustered.

    Pure orchestration over injected callables — no DB, no model — so the
    decision logic is unit-testable. A failure on one id is logged and skipped so
    one bad row never aborts the whole run.
    """
    processed = 0
    for news_id in ids:
        try:
            embedding = await embed(news_id, embedder)
            cluster_id = await cluster(news_id, embedding)
        except Exception as exc:
            log.warning("enrich_news_item_failed", news_id=news_id, error=str(exc))
            continue
        processed += 1
        log.debug("enriched_news_item", news_id=news_id, cluster_id=cluster_id)
    return processed


async def enrich_news_once(limit: int = 200) -> None:
    """Embed + cluster recent unembedded news items, deduping near-identical stories.

    Runs always-on: :func:`get_embedder` supplies the deterministic hash embedder
    when the ML extra / embeddings are disabled, so no feature flag gates this.
    """
    ids = await _recent_unembedded_ids(limit)
    if not ids:
        log.info("enrich_news_noop", reason="no unembedded news items")
        return

    embedder = get_embedder()

    async def _embed(news_id: int, emb: Embedder) -> list[float]:
        return await embed_news_item(news_id, embedder=emb)

    async def _cluster(news_id: int, embedding: list[float]) -> int:
        return await cluster_news_item(news_id, embedding)

    processed = await _enrich_ids(
        ids,
        embedder=embedder,
        embed=_embed,
        cluster=_cluster,
    )
    log.info("enrich_news_done", candidates=len(ids), processed=processed)
