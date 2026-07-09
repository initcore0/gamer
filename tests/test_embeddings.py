from __future__ import annotations

import math

import pytest

from gamer.db.models import EMBEDDING_DIM
from gamer.enrichment.embeddings import (
    HashEmbedder,
    average_embeddings,
    cosine_similarity,
    game_text,
    get_embedder,
    news_text,
)

# ── cosine_similarity math ───────────────────────────────────────────────────


def test_cosine_identical_is_one() -> None:
    v = [1.0, 2.0, 3.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one() -> None:
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_returns_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 2.0], [1.0])


# ── HashEmbedder ─────────────────────────────────────────────────────────────


def test_hash_embedder_is_384_dim_unit_vectors() -> None:
    emb = HashEmbedder()
    assert emb.dim == EMBEDDING_DIM
    (vec,) = emb.embed(["Hades"])
    assert len(vec) == EMBEDDING_DIM
    norm = math.sqrt(sum(x * x for x in vec))
    assert norm == pytest.approx(1.0)


def test_hash_embedder_is_deterministic() -> None:
    emb = HashEmbedder()
    a = emb.embed(["Hollow Knight"])[0]
    b = emb.embed(["Hollow Knight"])[0]
    assert a == b
    # Self-similarity of a deterministic unit vector is exactly 1.
    assert cosine_similarity(a, b) == pytest.approx(1.0)


def test_hash_embedder_distinguishes_texts() -> None:
    emb = HashEmbedder()
    a = emb.embed(["Celeste"])[0]
    b = emb.embed(["Doom Eternal"])[0]
    assert a != b
    # Different texts should not be near-identical.
    assert cosine_similarity(a, b) < 0.99


def test_hash_embedder_batches() -> None:
    emb = HashEmbedder()
    out = emb.embed(["a", "b", "c"])
    assert len(out) == 3
    assert all(len(v) == EMBEDDING_DIM for v in out)


def test_hash_embedder_empty_batch() -> None:
    assert HashEmbedder().embed([]) == []


# ── get_embedder factory ─────────────────────────────────────────────────────


def test_get_embedder_defaults_to_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    from gamer.config import get_settings

    monkeypatch.delenv("GAMER_EMBEDDINGS__ENABLED", raising=False)
    get_settings.cache_clear()
    assert isinstance(get_embedder(), HashEmbedder)


# ── average_embeddings ───────────────────────────────────────────────────────


def test_average_embeddings_means_elementwise() -> None:
    out = average_embeddings([[0.0, 2.0], [2.0, 4.0]])
    assert out == [1.0, 3.0]


def test_average_embeddings_empty_is_none() -> None:
    assert average_embeddings([]) is None


def test_average_embeddings_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        average_embeddings([[1.0, 2.0], [1.0]])


# ── text builders ────────────────────────────────────────────────────────────


def test_game_text_includes_genres() -> None:
    text = game_text("Hades", ["Roguelike", "Action"])
    assert "Hades" in text
    assert "Roguelike" in text and "Action" in text


def test_game_text_without_genres() -> None:
    assert game_text("Hades", []) == "Hades."


def test_news_text_joins_title_and_body() -> None:
    assert news_text("Patch 1.2", "Fixes crash") == "Patch 1.2\n\nFixes crash"
    assert news_text("Patch 1.2", None) == "Patch 1.2"
