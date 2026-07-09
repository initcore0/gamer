from __future__ import annotations

from gamer.bot.handlers import (
    format_movers_reply,
    format_scored_reply,
    parse_feedback_action,
)
from gamer.db.models import FeedbackVerdict
from gamer.scoring.base import ScoredRecommendation
from gamer.signals.movers import Mover


def test_parse_feedback_action_valid() -> None:
    assert parse_feedback_action("feedback:up:42") == (FeedbackVerdict.UP, 42)
    assert parse_feedback_action("feedback:played:7") == (FeedbackVerdict.PLAYED, 7)


def test_parse_feedback_action_rejects_malformed() -> None:
    assert parse_feedback_action("feedback:up") is None
    assert parse_feedback_action("other:up:1") is None
    assert parse_feedback_action("feedback:sideways:1") is None
    assert parse_feedback_action("feedback:up:notanint") is None
    assert parse_feedback_action("") is None


def test_format_movers_reply_empty() -> None:
    assert "still gathering" in format_movers_reply([])


def test_format_movers_reply_lists_games() -> None:
    reply = format_movers_reply(
        [Mover(game_id=1, name="Hades", platform_app_id=1145360, latest=12000, baseline=8000)]
    )
    assert "Hades" in reply
    assert "+4,000" in reply


def test_format_scored_reply_shows_top_reasons_and_penalty() -> None:
    rec = ScoredRecommendation(
        game_id=1,
        name="Hades",
        score=0.42,
        breakdown={
            "momentum": {"weighted": 0.30, "reason": "surging players"},
            "fit": {"weighted": 0.05, "reason": "matches taste"},
            "freshness": {"weighted": 0.01, "reason": "old release"},
            "penalty:cooldown": {"multiplier": 0.5, "reason": "on cooldown"},
        },
    )
    reply = format_scored_reply([rec])
    assert "Hades" in reply
    assert "0.42" in reply
    # Top two component reasons surface; the lowest-weighted one is trimmed.
    assert "surging players" in reply
    assert "matches taste" in reply
    assert "old release" not in reply
    # Applied penalty is flagged.
    assert "on cooldown" in reply
