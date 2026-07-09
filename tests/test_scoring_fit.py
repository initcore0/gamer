from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gamer.enrichment.embeddings import HashEmbedder, game_text
from gamer.scoring.base import Candidate, ScoringContext
from gamer.scoring.components.fit import FitComponent, build_profile_embedding


def _ctx(profile: list[float] | None = None) -> ScoringContext:
    return ScoringContext(now=datetime(2026, 7, 9, tzinfo=UTC), profile_embedding=profile)


def _cand(name: str = "Hades", genres: list[str] | None = None) -> Candidate:
    return Candidate(
        game_id=1,
        platform_app_id=1145360,
        name=name,
        genres=genres or ["Roguelike", "Action"],
    )


async def test_fit_zero_without_profile() -> None:
    comp = FitComponent(embedder=HashEmbedder())
    cs = await comp.score(_cand(), _ctx(profile=None))
    assert cs.value == 0.0
    assert cs.reason == "no taste profile yet"
    assert cs.detail["cosine"] is None


async def test_fit_perfect_when_profile_is_the_game_itself() -> None:
    emb = HashEmbedder()
    cand = _cand()
    # Profile == the game's own embedding → cosine 1.0 → value 1.0.
    profile = emb.embed([game_text(cand.name, cand.genres)])[0]
    comp = FitComponent(embedder=emb)
    cs = await comp.score(cand, _ctx(profile=profile))
    assert cs.value == pytest.approx(1.0)
    assert cs.detail["cosine"] == pytest.approx(1.0)
    assert cs.reason == "strong match to your taste"


async def test_fit_value_in_unit_interval_for_unrelated_profile() -> None:
    emb = HashEmbedder()
    # A profile built from an unrelated game.
    profile = emb.embed([game_text("Farming Simulator", ["Simulation"])])[0]
    comp = FitComponent(embedder=emb)
    cs = await comp.score(_cand(), _ctx(profile=profile))
    assert 0.0 <= cs.value <= 1.0


async def test_fit_never_negative() -> None:
    # Even with an adversarial (negated) profile, value clamps at 0.
    emb = HashEmbedder()
    cand = _cand()
    game_vec = emb.embed([game_text(cand.name, cand.genres)])[0]
    negated = [-x for x in game_vec]
    comp = FitComponent(embedder=emb)
    cs = await comp.score(cand, _ctx(profile=negated))
    assert cs.value == 0.0
    assert cs.detail["cosine"] == pytest.approx(-1.0)


def test_build_profile_embedding_averages() -> None:
    profile = build_profile_embedding([[0.0, 2.0], [2.0, 4.0]])
    assert profile == [1.0, 3.0]


def test_build_profile_embedding_empty_is_none() -> None:
    assert build_profile_embedding([]) is None


def test_build_profile_embedding_matches_single_game() -> None:
    emb = HashEmbedder()
    vec = emb.embed([game_text("Hades", ["Roguelike"])])[0]
    profile = build_profile_embedding([vec])
    assert profile == vec
