"""Embeddings service — game/taste vectors + news dedup (PLAN.md §4.4).

Embeddings power two things:

* **News dedup / clustering** — near-identical stories (same announcement
  reworded across outlets) are collapsed by cosine distance in pgvector, so the
  digest never repeats itself.
* **The ``fit`` score component** — a game's representative vector is compared to
  the streamer's learned taste vector (``streamer_prefs.profile_embedding``).

Two :class:`Embedder` implementations back this:

* :class:`LocalEmbedder` wraps ``sentence-transformers`` and runs the configured
  model (default ``bge-small-en-v1.5``, 384-dim) on the GPU box. The model is
  **lazy-loaded** — importing this module must stay cheap, and CI has no GPU.
* :class:`HashEmbedder` is a deterministic, dependency-free fallback that maps
  text to a fixed 384-dim unit vector by hashing. It is used when embeddings are
  disabled and in unit tests, so tests never load a model or touch a GPU.

:func:`get_embedder` picks the implementation from ``settings.embeddings.enabled``.

The pgvector-backed helpers (:func:`embed_news_item`, :func:`find_near_duplicates`)
are marked ``@pytest.mark.integration`` in the tests — the pure math helpers
(:func:`cosine_similarity`) are unit-testable without a DB or model.
"""

from __future__ import annotations

import hashlib
import math
import struct
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from gamer.config import get_settings
from gamer.db.engine import session_scope
from gamer.db.models import EMBEDDING_DIM, NewsItem
from gamer.logging import get_logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

log = get_logger("enrichment.embeddings")


# ── Pure math ────────────────────────────────────────────────────────────────


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors, in ``[-1, 1]``.

    Returns ``0.0`` if either vector is all-zeros (undefined direction). Pure and
    dependency-free so it is unit-testable without a model or a database.
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} != {len(b)}")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


def average_embeddings(embeddings: list[list[float]]) -> list[float] | None:
    """Element-wise mean of embeddings. ``None`` if the input is empty.

    All rows must share the same dimensionality. Used to fold a set of liked-game
    vectors into a single streamer taste vector.
    """
    if not embeddings:
        return None
    dim = len(embeddings[0])
    acc = [0.0] * dim
    for vec in embeddings:
        if len(vec) != dim:
            raise ValueError(f"embedding length mismatch: {len(vec)} != {dim}")
        for i, v in enumerate(vec):
            acc[i] += v
    n = float(len(embeddings))
    return [v / n for v in acc]


# ── Embedder abstraction ─────────────────────────────────────────────────────


@runtime_checkable
class Embedder(Protocol):
    """Maps a batch of texts to a batch of fixed-dimension float vectors."""

    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """``sentence-transformers`` embedder. The model is loaded lazily on first
    :meth:`embed` so importing this module (and CI with no GPU) stays cheap.
    """

    def __init__(self, model_name: str | None = None, dim: int | None = None) -> None:
        settings = get_settings().embeddings
        self.model_name = model_name or settings.model
        self.dim = dim if dim is not None else settings.dim
        self._model: SentenceTransformer | None = None

    def _load(self) -> SentenceTransformer:
        if self._model is None:
            # Imported here, not at module top: the dependency is heavy and only
            # present with the optional ``ml`` extra installed.
            from sentence_transformers import SentenceTransformer

            log.info("loading embedding model", model=self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(x) for x in row] for row in vectors]


class HashEmbedder:
    """Deterministic, dependency-free fallback embedder.

    Maps each text to a fixed-dim **unit** vector by hashing (SHA-256 expanded to
    fill ``dim`` floats, then L2-normalized). No model, no GPU, no I/O — same text
    always yields the same vector, which makes it ideal for unit tests and for
    running when ``embeddings.enabled`` is False.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        # Expand the digest deterministically until we have `dim` float words.
        raw = b""
        counter = 0
        needed = self.dim * 4  # 4 bytes per float32-ish word
        while len(raw) < needed:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            raw += digest
            counter += 1
        floats: list[float] = []
        for i in range(self.dim):
            (word,) = struct.unpack_from(">I", raw, i * 4)
            # Map the uint32 into a centered [-1, 1] component.
            floats.append((word / 0xFFFFFFFF) * 2.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in floats))
        if norm == 0.0:
            # Degenerate; return a canonical unit vector.
            unit = [0.0] * self.dim
            unit[0] = 1.0
            return unit
        return [v / norm for v in floats]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


def get_embedder() -> Embedder:
    """Factory: :class:`LocalEmbedder` when embeddings are enabled, else the
    deterministic :class:`HashEmbedder` fallback.
    """
    settings = get_settings().embeddings
    if settings.enabled:
        return LocalEmbedder()
    return HashEmbedder(dim=settings.dim)


# ── Text builders ────────────────────────────────────────────────────────────


def game_text(name: str, genres: list[str]) -> str:
    """Representative text for a game: its name plus genres, used to embed it for
    the ``fit`` component. Kept here so the embedding is defined in one place.
    """
    if genres:
        return f"{name}. Genres: {', '.join(genres)}."
    return f"{name}."


def news_text(title: str, body: str | None) -> str:
    """Representative text for a news item (title + body) for dedup/clustering."""
    if body:
        return f"{title}\n\n{body}"
    return title


# ── pgvector-backed helpers (integration) ────────────────────────────────────


async def embed_news_item(news_id: int, *, embedder: Embedder | None = None) -> list[float]:
    """Compute and persist ``NewsItem.embedding`` for one news row.

    Returns the embedding written. Talks to the database → integration-only.
    """
    emb = embedder or get_embedder()
    async with session_scope() as session:
        item = await session.get(NewsItem, news_id)
        if item is None:
            raise LookupError(f"news item {news_id} not found")
        vector = emb.embed([news_text(item.title, item.body)])[0]
        item.embedding = vector
        log.info("embedded news item", news_id=news_id, dim=len(vector))
        return vector


async def find_near_duplicates(
    embedding: list[float],
    *,
    threshold: float = 0.15,
    limit: int = 20,
    exclude_id: int | None = None,
) -> list[tuple[int, float]]:
    """Return ``(news_id, cosine_distance)`` for stories within ``threshold``
    cosine distance of ``embedding``, nearest first.

    Uses pgvector's ``<=>`` cosine-distance operator (distance ``= 1 - cosine
    similarity``); a small ``threshold`` (~0.15) means "near-identical story".
    Talks to the database → integration-only.
    """
    from sqlalchemy import select

    distance = NewsItem.embedding.cosine_distance(embedding).label("distance")
    stmt = (
        select(NewsItem.id, distance)
        .where(NewsItem.embedding.is_not(None))
        .where(distance <= threshold)
        .order_by(distance)
        .limit(limit)
    )
    if exclude_id is not None:
        stmt = stmt.where(NewsItem.id != exclude_id)

    async with session_scope() as session:
        rows = await session.execute(stmt)
        return [(int(nid), float(dist)) for nid, dist in rows.all()]


async def cluster_news_item(
    news_id: int,
    embedding: list[float],
    *,
    threshold: float = 0.15,
) -> int:
    """Assign ``NewsItem.cluster_id`` for ``news_id`` by joining the nearest
    existing cluster within ``threshold``, or starting a fresh cluster.

    Returns the ``cluster_id`` assigned. Talks to the database → integration-only.
    """
    from sqlalchemy import func, select

    async with session_scope() as session:
        neighbors = await find_near_duplicates(embedding, threshold=threshold, exclude_id=news_id)
        cluster_id: int | None = None
        for nid, _dist in neighbors:
            neighbor = await session.get(NewsItem, nid)
            if neighbor is not None and neighbor.cluster_id is not None:
                cluster_id = neighbor.cluster_id
                break

        if cluster_id is None:
            # Start a new cluster: max(cluster_id) + 1, or 1 for the first ever.
            current_max = await session.scalar(select(func.max(NewsItem.cluster_id)))
            cluster_id = (current_max or 0) + 1

        item = await session.get(NewsItem, news_id)
        if item is None:
            raise LookupError(f"news item {news_id} not found")
        item.cluster_id = cluster_id
        log.info("clustered news item", news_id=news_id, cluster_id=cluster_id)
        return cluster_id
