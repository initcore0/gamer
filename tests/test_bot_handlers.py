from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.filters import CommandObject

from gamer.bot.handlers import (
    cmd_track,
    cmd_untrack,
    format_movers_reply,
    format_scored_reply,
    help_text,
    parse_feedback_action,
    router,
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


@dataclass
class _FakeMessage:
    """Minimal Message stand-in that records what a handler answers."""

    replies: list[str] = field(default_factory=list)

    async def answer(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


async def test_track_without_arg_shows_usage() -> None:
    msg = _FakeMessage()
    await cmd_track(msg, CommandObject(command="track", args=None))  # type: ignore[arg-type]
    assert len(msg.replies) == 1
    assert "Usage" in msg.replies[0]
    assert "/track" in msg.replies[0]


async def test_untrack_without_arg_shows_usage() -> None:
    msg = _FakeMessage()
    await cmd_untrack(msg, CommandObject(command="untrack", args=""))  # type: ignore[arg-type]
    assert len(msg.replies) == 1
    assert "Usage" in msg.replies[0]
    assert "/untrack" in msg.replies[0]


def test_track_and_untrack_handlers_registered() -> None:
    names = {h.callback.__name__ for h in router.message.handlers}
    assert "cmd_track" in names
    assert "cmd_untrack" in names


def test_help_text_lists_track_commands() -> None:
    text = help_text()
    assert "/track" in text
    assert "/untrack" in text


def test_help_text_lists_commands_and_is_valid_html() -> None:
    text = help_text()
    for cmd in ("/recommend", "/why", "/mute", "/prefs", "/digest", "/help"):
        assert cmd in text
    # HTML parse_mode: angle brackets in placeholders must be escaped.
    assert "&lt;game&gt;" in text
    assert "<game>" not in text
    # Balanced bold tags.
    assert text.count("<b>") == text.count("</b>")


def test_help_and_start_handlers_registered() -> None:
    from gamer.bot.handlers import router

    # /help and /start (CommandStart) both route to cmd_help — the router has a
    # message handler registered for them.
    assert any(h.callback.__name__ == "cmd_help" for h in router.message.handlers)
