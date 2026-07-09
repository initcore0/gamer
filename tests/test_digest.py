from __future__ import annotations

from datetime import date

from gamer.notify.base import Channel
from gamer.notify.digest import build_digest, build_scored_digest
from gamer.scoring.base import ScoredRecommendation
from gamer.signals.movers import Mover


def test_digest_with_movers_is_deterministic() -> None:
    movers = [
        Mover(game_id=1, name="Hades", platform_app_id=1145360, latest=12000, baseline=8000),
        Mover(game_id=2, name="Celeste", platform_app_id=504230, latest=3000, baseline=2500),
    ]
    n = build_digest(movers, channel=Channel.TELEGRAM_GROUP, for_day=date(2026, 7, 9))
    assert n.channel is Channel.TELEGRAM_GROUP
    assert n.dedup_key == "digest:telegram_group:2026-07-09"
    assert "Hades" in n.text
    assert "+4,000" in n.text  # delta rendered
    assert "+50%" in n.text  # pct rendered
    assert n.meta["parse_mode"] == "HTML"


def test_digest_empty_state() -> None:
    n = build_digest([], for_day=date(2026, 7, 9))
    assert "still gathering" in n.text
    assert n.dedup_key == "digest:telegram_group:2026-07-09"


def test_scored_digest_uses_top_reason() -> None:
    recs = [
        ScoredRecommendation(
            game_id=1,
            name="Rising Star",
            score=0.44,
            breakdown={
                "momentum": {"weighted": 0.30, "reason": "players surging"},
                "hype": {"weighted": 0.04, "reason": "news burst"},
                "penalty:cooldown": {"multiplier": 0.5, "reason": "on cooldown"},
            },
        )
    ]
    n = build_scored_digest(recs, for_day=date(2026, 7, 9))
    assert "Rising Star" in n.text
    assert "players surging" in n.text  # highest-weighted reason wins
    assert "news burst" not in n.text  # only the top reason shown
    assert n.dedup_key == "digest:telegram_group:2026-07-09"


def test_scored_digest_empty() -> None:
    n = build_scored_digest([], for_day=date(2026, 7, 9))
    assert "still gathering" in n.text


def _one_rec() -> list[ScoredRecommendation]:
    return [
        ScoredRecommendation(
            game_id=1,
            name="Rising Star",
            score=0.44,
            breakdown={"momentum": {"weighted": 0.30, "reason": "players surging"}},
        )
    ]


def test_scored_digest_renders_summary_when_provided() -> None:
    n = build_scored_digest(
        _one_rec(),
        for_day=date(2026, 7, 9),
        summary="Roguelikes are having a moment today.",
    )
    assert "<i>Roguelikes are having a moment today.</i>" in n.text
    # Blurb sits above the picks.
    assert n.text.index("Roguelikes") < n.text.index("Rising Star")
    assert "Rising Star" in n.text


def test_scored_digest_escapes_llm_markup_in_summary() -> None:
    # LLM output is untrusted: raw < or & must not reach Telegram's HTML parser,
    # or the whole send fails with a permanent bad request.
    n = build_scored_digest(
        _one_rec(),
        for_day=date(2026, 7, 9),
        summary='Big day <b>for "roguelikes" & deckbuilders</b>',
    )
    assert "<b>for" not in n.text
    assert "&lt;b&gt;for &quot;roguelikes&quot; &amp; deckbuilders&lt;/b&gt;" in n.text
    # The digest's own markup is still intact.
    assert n.text.startswith("<b>🎮 What to stream")


def test_scored_digest_none_summary_matches_current_output() -> None:
    # summary=None (and the default) must be byte-for-byte the pre-LLM digest.
    baseline = build_scored_digest(_one_rec(), for_day=date(2026, 7, 9))
    explicit_none = build_scored_digest(_one_rec(), for_day=date(2026, 7, 9), summary=None)
    assert explicit_none.text == baseline.text
    assert "<i>" not in baseline.text


def test_mover_math() -> None:
    m = Mover(game_id=1, name="X", platform_app_id=1, latest=150, baseline=100)
    assert m.delta == 50
    assert m.pct == 50.0
    zero = Mover(game_id=2, name="Y", platform_app_id=2, latest=10, baseline=0)
    assert zero.pct is None  # guards divide-by-zero baseline
