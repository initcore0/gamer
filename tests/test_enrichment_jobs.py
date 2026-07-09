from __future__ import annotations

import pytest

from gamer.enrichment.embeddings import HashEmbedder
from gamer.enrichment.jobs import _enrich_ids


async def test_enrich_ids_embeds_then_clusters_each_id() -> None:
    embedder = HashEmbedder(dim=4)
    embedded: list[int] = []
    clustered: list[tuple[int, list[float]]] = []

    async def embed(news_id: int, emb: object) -> list[float]:
        embedded.append(news_id)
        return [0.1, 0.2, 0.3, 0.4]

    async def cluster(news_id: int, embedding: list[float]) -> int:
        clustered.append((news_id, embedding))
        return 7  # everyone joins the same cluster (near-duplicates)

    processed = await _enrich_ids([10, 11, 12], embedder=embedder, embed=embed, cluster=cluster)

    assert processed == 3
    assert embedded == [10, 11, 12]  # embedded in order
    assert [nid for nid, _ in clustered] == [10, 11, 12]  # clustered in order
    # The embedding computed for each id is the one passed to the clusterer.
    assert all(vec == [0.1, 0.2, 0.3, 0.4] for _, vec in clustered)


async def test_enrich_ids_empty_is_noop() -> None:
    async def embed(news_id: int, emb: object) -> list[float]:  # pragma: no cover
        raise AssertionError("embed must not be called for empty input")

    async def cluster(news_id: int, embedding: list[float]) -> int:  # pragma: no cover
        raise AssertionError("cluster must not be called for empty input")

    processed = await _enrich_ids([], embedder=HashEmbedder(dim=4), embed=embed, cluster=cluster)
    assert processed == 0


async def test_enrich_ids_skips_failures_without_aborting() -> None:
    calls: list[int] = []

    async def embed(news_id: int, emb: object) -> list[float]:
        calls.append(news_id)
        if news_id == 2:
            raise RuntimeError("embed blew up on id 2")
        return [0.0, 0.0, 0.0, 1.0]

    async def cluster(news_id: int, embedding: list[float]) -> int:
        return news_id

    processed = await _enrich_ids(
        [1, 2, 3], embedder=HashEmbedder(dim=4), embed=embed, cluster=cluster
    )

    # id 2 failed and was skipped; the run continued to id 3.
    assert processed == 2
    assert calls == [1, 2, 3]


async def test_enrich_ids_cluster_failure_is_skipped() -> None:
    async def embed(news_id: int, emb: object) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    async def cluster(news_id: int, embedding: list[float]) -> int:
        raise RuntimeError("cluster failure")

    processed = await _enrich_ids(
        [1, 2], embedder=HashEmbedder(dim=4), embed=embed, cluster=cluster
    )
    assert processed == 0  # all clustering failed → nothing counted, no raise


@pytest.mark.integration
async def test_enrich_news_once_real_db() -> None:
    """Real embed + cluster path against Postgres/pgvector. DB-only."""
    from gamer.enrichment.jobs import enrich_news_once

    # Smoke: with no unembedded rows this is a no-op; with rows it embeds+clusters.
    await enrich_news_once(limit=10)
