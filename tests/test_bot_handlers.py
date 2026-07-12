from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardMarkup

from gamer.bot import handlers
from gamer.bot.handlers import (
    GenreAction,
    build_genres_keyboard,
    cmd_genres,
    cmd_subscribe,
    cmd_track,
    cmd_unsubscribe,
    cmd_untrack,
    format_movers_reply,
    format_scored_reply,
    genre_digest,
    help_text,
    on_genre,
    paginate,
    parse_feedback_action,
    parse_genre_action,
    resolve_genre,
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
class _FakeChat:
    """Minimal aiogram Chat stand-in: a private DM chat with a positive id."""

    id: int = 12345
    type: str = "private"
    title: str | None = None


@dataclass
class _FakeUser:
    full_name: str = "Test User"


@dataclass
class _FakeMessage:
    """Minimal Message stand-in that records what a handler answers."""

    replies: list[str] = field(default_factory=list)
    chat: _FakeChat = field(default_factory=_FakeChat)
    from_user: _FakeUser = field(default_factory=_FakeUser)

    async def answer(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


@dataclass
class _FakeMessageKb:
    """Message stand-in that also records the reply_markup a handler attaches."""

    replies: list[str] = field(default_factory=list)
    markups: list[object] = field(default_factory=list)
    chat: _FakeChat = field(default_factory=_FakeChat)
    from_user: _FakeUser = field(default_factory=_FakeUser)

    async def answer(
        self, text: str, parse_mode: str | None = None, reply_markup: object = None
    ) -> None:
        self.replies.append(text)
        self.markups.append(reply_markup)


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


def test_help_text_lists_subscribe_commands() -> None:
    text = help_text()
    assert "/subscribe" in text
    assert "/unsubscribe" in text


# ── /subscribe /unsubscribe (M7) ──────────────────────────────────────────────


def test_resolve_genre_case_insensitive_returns_canonical() -> None:
    canonical, suggestions = resolve_genre("puzzle", ["Puzzle", "RPG", "Indie"])
    assert canonical == "Puzzle"
    assert suggestions == []


def test_resolve_genre_miss_returns_suggestions() -> None:
    canonical, suggestions = resolve_genre("puzzel", ["Puzzle", "RPG", "Indie"])
    assert canonical is None
    assert "Puzzle" in suggestions
    assert len(suggestions) <= 5


async def test_subscribe_without_arg_shows_usage() -> None:
    msg = _FakeMessage()
    await cmd_subscribe(msg, CommandObject(command="subscribe", args=None))  # type: ignore[arg-type]
    assert "Usage" in msg.replies[0]
    assert "/subscribe" in msg.replies[0]


async def test_unsubscribe_without_arg_shows_usage() -> None:
    msg = _FakeMessage()
    await cmd_unsubscribe(msg, CommandObject(command="unsubscribe", args=""))  # type: ignore[arg-type]
    assert "Usage" in msg.replies[0]
    assert "/unsubscribe" in msg.replies[0]


async def test_subscribe_unknown_genre_suggests_close_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_known() -> list[str]:
        return ["Puzzle", "RPG", "Indie"]

    monkeypatch.setattr(handlers, "known_genres", _fake_known)
    msg = _FakeMessage()
    await cmd_subscribe(msg, CommandObject(command="subscribe", args="puzzel"))  # type: ignore[arg-type]
    assert "No genre matching" in msg.replies[0]
    assert "Puzzle" in msg.replies[0]


async def test_subscribe_registered() -> None:
    names = {h.callback.__name__ for h in router.message.handlers}
    assert "cmd_subscribe" in names
    assert "cmd_unsubscribe" in names


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


# ── /genres button panel (M7 UX) ──────────────────────────────────────────────


def test_help_text_lists_genres_command() -> None:
    assert "/genres" in help_text()


def test_paginate_empty() -> None:
    items, pages = paginate([], 0)
    assert items == []
    assert pages == 1


def test_paginate_single_page() -> None:
    items, pages = paginate(["a", "b", "c"], 0, per_page=16)
    assert items == ["a", "b", "c"]
    assert pages == 1


def test_paginate_multi_and_clamps() -> None:
    genres = [f"g{i}" for i in range(20)]
    first, pages = paginate(genres, 0, per_page=16)
    assert len(first) == 16
    assert pages == 2
    second, _ = paginate(genres, 1, per_page=16)
    assert len(second) == 4
    # Out-of-range page clamps to the last valid page.
    clamped, _ = paginate(genres, 99, per_page=16)
    assert clamped == second


def test_build_genres_keyboard_empty_returns_none() -> None:
    assert build_genres_keyboard([], set(), 0) is None


def test_build_genres_keyboard_marks_grid_and_no_nav() -> None:
    kb = build_genres_keyboard(["Puzzle", "rpg", "Indie"], {"puzzle"}, 0)
    assert isinstance(kb, InlineKeyboardMarkup)
    texts = [b.text for row in kb.inline_keyboard for b in row]
    # Sorted case-insensitively: Indie, Puzzle, rpg.
    assert texts == ["Indie", "✅ Puzzle", "rpg"]
    # 2 per row → rows of [2, 1], no nav row (single page).
    assert [len(r) for r in kb.inline_keyboard] == [2, 1]


def test_build_genres_keyboard_nav_rows() -> None:
    genres = [f"g{i:02d}" for i in range(20)]
    kb0 = build_genres_keyboard(genres, set(), 0)
    assert kb0 is not None
    nav0 = kb0.inline_keyboard[-1]
    # First page: only page label + ▶️ (no ◀️).
    assert [b.text for b in nav0] == ["page 1/2", "▶️"]
    assert nav0[0].callback_data == "genre:noop"

    kb1 = build_genres_keyboard(genres, set(), 1)
    assert kb1 is not None
    nav1 = kb1.inline_keyboard[-1]
    assert [b.text for b in nav1] == ["◀️", "page 2/2"]
    assert nav1[0].callback_data == "genre:p:0"


def test_callback_data_within_64_bytes_adversarial() -> None:
    long_genres = ["Ω" * 200, "very long genre name " * 20, "普通话游戏类型" * 30]
    kb = build_genres_keyboard(long_genres, set(long_genres), 0)
    assert kb is not None
    for row in kb.inline_keyboard:
        for b in row:
            assert b.callback_data is not None
            assert len(b.callback_data.encode("utf-8")) <= 64


def test_parse_genre_action_valid() -> None:
    d = genre_digest("Puzzle")
    assert parse_genre_action(f"genre:t:{d}:3") == GenreAction("toggle", d, 3)
    assert parse_genre_action("genre:p:2") == GenreAction("page", None, 2)
    assert parse_genre_action("genre:noop") == GenreAction("noop", None, 0)


def test_parse_genre_action_rejects_garbage() -> None:
    assert parse_genre_action("") is None
    assert parse_genre_action("other:t:abc:1") is None
    assert parse_genre_action("genre:t") is None
    assert parse_genre_action("genre:t:xyz:1") is None  # non-hex digest
    assert parse_genre_action("genre:t:" + "a" * 11 + ":1") is None  # wrong length
    assert parse_genre_action("genre:t:" + "a" * 12 + ":-1") is None  # negative page
    assert parse_genre_action("genre:p:notanint") is None
    assert parse_genre_action("genre:noop:extra") is None
    assert parse_genre_action("genre:t:" + "a" * 12) is None  # missing page


# --- callback handler tests -------------------------------------------------


@dataclass
class _FakeCbMessage:
    """Message stand-in that records edit_reply_markup calls (and can raise)."""

    edited: list[object] = field(default_factory=list)
    raise_not_modified: bool = False

    async def edit_reply_markup(self, reply_markup: object = None) -> None:
        if self.raise_not_modified:
            raise TelegramBadRequest(method=None, message="Bad Request: message is not modified")  # type: ignore[arg-type]
        self.edited.append(reply_markup)


@dataclass
class _FakeCallback:
    data: str
    message: _FakeCbMessage | None = None
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None) -> None:
        self.answers.append(text)


def _patch_genre_env(
    monkeypatch: pytest.MonkeyPatch, genres: list[str], subscribed: list[str]
) -> dict[str, object]:
    state: dict[str, object] = {"subscribed": list(subscribed), "toggle_calls": []}

    async def _known() -> list[str]:
        return genres

    async def _subs(key: str = "default") -> set[str]:
        return set(state["subscribed"])  # type: ignore[arg-type]

    async def _toggle(
        canonical: str, key: str = "default", *, label: str | None = None
    ) -> tuple[bool, int]:
        calls = state["toggle_calls"]
        assert isinstance(calls, list)
        calls.append(canonical)
        subs = state["subscribed"]
        assert isinstance(subs, list)
        lowered = canonical.lower()
        if any(s.lower() == lowered for s in subs):
            state["subscribed"] = [s for s in subs if s.lower() != lowered]
            return False, 0
        state["subscribed"] = [*subs, canonical]
        return True, 7

    monkeypatch.setattr(handlers, "known_genres", _known)
    monkeypatch.setattr(handlers, "_subscribed_set", _subs)
    monkeypatch.setattr(handlers, "_toggle_subscription", _toggle)
    return state


async def test_on_genre_subscribe_tracks_and_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _patch_genre_env(monkeypatch, ["Puzzle", "RPG"], [])
    cb = _FakeCallback(data=f"genre:t:{genre_digest('Puzzle')}:0", message=_FakeCbMessage())
    await on_genre(cb)  # type: ignore[arg-type]
    assert state["toggle_calls"] == ["Puzzle"]
    assert cb.answers and "Subscribed to Puzzle" in (cb.answers[0] or "")
    assert "tracking 7 games" in (cb.answers[0] or "")
    assert cb.message is not None and len(cb.message.edited) == 1


async def test_on_genre_unsubscribe(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_genre_env(monkeypatch, ["Puzzle"], ["Puzzle"])
    cb = _FakeCallback(data=f"genre:t:{genre_digest('Puzzle')}:0", message=_FakeCbMessage())
    await on_genre(cb)  # type: ignore[arg-type]
    assert cb.answers and "Unsubscribed from Puzzle" in (cb.answers[0] or "")
    assert cb.message is not None and len(cb.message.edited) == 1


async def test_on_genre_unknown_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_genre_env(monkeypatch, ["Puzzle"], [])
    cb = _FakeCallback(data="genre:t:" + "a" * 12 + ":0", message=_FakeCbMessage())
    await on_genre(cb)  # type: ignore[arg-type]
    assert cb.answers and "gone" in (cb.answers[0] or "")


async def test_on_genre_not_modified_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_genre_env(monkeypatch, ["Puzzle"], [])
    msg = _FakeCbMessage(raise_not_modified=True)
    cb = _FakeCallback(data=f"genre:t:{genre_digest('Puzzle')}:0", message=msg)
    await on_genre(cb)  # type: ignore[arg-type]  # must not raise
    assert cb.answers  # the callback was still answered


async def test_on_genre_noop_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_genre_env(monkeypatch, ["Puzzle"], [])
    cb = _FakeCallback(data="genre:noop", message=_FakeCbMessage())
    await on_genre(cb)  # type: ignore[arg-type]
    assert cb.answers == [None]
    assert cb.message is not None and cb.message.edited == []


async def test_on_genre_page_nav_edits(monkeypatch: pytest.MonkeyPatch) -> None:
    genres = [f"g{i:02d}" for i in range(20)]
    _patch_genre_env(monkeypatch, genres, [])
    cb = _FakeCallback(data="genre:p:1", message=_FakeCbMessage())
    await on_genre(cb)  # type: ignore[arg-type]
    assert cb.answers == [None]
    assert cb.message is not None and len(cb.message.edited) == 1


async def _stub_get_prefs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub _get_prefs so cmd_genres doesn't touch the DB when ensuring a profile."""
    from gamer.db.models import StreamerPref

    async def _get(key: str, *, label: str | None = None) -> StreamerPref:
        return StreamerPref(key=key, label=label)

    monkeypatch.setattr(handlers, "_get_prefs", _get)


async def test_cmd_genres_empty_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _known() -> list[str]:
        return []

    async def _subs(key: str = "default") -> set[str]:
        return set()

    monkeypatch.setattr(handlers, "known_genres", _known)
    monkeypatch.setattr(handlers, "_subscribed_set", _subs)
    await _stub_get_prefs(monkeypatch)
    msg = _FakeMessageKb()
    await cmd_genres(msg)  # type: ignore[arg-type]
    assert msg.replies and "No genres" in msg.replies[0]
    assert msg.markups == [None]


async def test_cmd_genres_attaches_keyboard(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _known() -> list[str]:
        return ["Puzzle", "RPG"]

    async def _subs(key: str = "default") -> set[str]:
        return {"Puzzle"}

    monkeypatch.setattr(handlers, "known_genres", _known)
    monkeypatch.setattr(handlers, "_subscribed_set", _subs)
    await _stub_get_prefs(monkeypatch)
    msg = _FakeMessageKb()
    await cmd_genres(msg)  # type: ignore[arg-type]
    assert "Tap a genre" in msg.replies[0]
    assert isinstance(msg.markups[0], InlineKeyboardMarkup)


def test_genre_handlers_registered() -> None:
    msg_names = {h.callback.__name__ for h in router.message.handlers}
    cb_names = {h.callback.__name__ for h in router.callback_query.handlers}
    assert "cmd_genres" in msg_names
    assert "on_genre" in cb_names


async def test_genre_callback_stale_panel_still_answers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Editing a >48h-old panel raises TelegramBadRequest — the tap must still be
    answered (no endless spinner), not re-raised out of the handler."""
    from aiogram.exceptions import TelegramBadRequest

    from gamer.bot import handlers

    answers: list[str | None] = []

    class _FakeMsg:
        async def edit_reply_markup(self, reply_markup=None):  # type: ignore[no-untyped-def]
            raise TelegramBadRequest(method=None, message="message can't be edited")

    class _FakeCallback:
        data = "genre:p:0"
        message = _FakeMsg()

        async def answer(self, text: str | None = None) -> None:
            answers.append(text)

    async def fake_known_genres() -> list[str]:
        return ["Puzzle"]

    async def fake_subscribed_set(key: str = "default") -> set[str]:
        return set()

    monkeypatch.setattr(handlers, "known_genres", fake_known_genres)
    monkeypatch.setattr(handlers, "_subscribed_set", fake_subscribed_set)

    await handlers.on_genre(_FakeCallback())  # type: ignore[arg-type]
    assert answers, "callback was never answered"
    assert "stale" in (answers[-1] or "")


# ── Multi-user: key derivation, labels, legacy adoption ───────────────────────


def test_resolve_key_from_dm_message() -> None:
    from gamer.bot.handlers import _resolve_key

    msg = _FakeMessage(chat=_FakeChat(id=42))
    assert _resolve_key(msg) == "42"


def test_resolve_key_falls_back_to_legacy_when_no_chat() -> None:
    from gamer.bot.handlers import LEGACY_KEY, _resolve_key

    assert _resolve_key(object()) == LEGACY_KEY


def test_label_for_dm_uses_full_name() -> None:
    from gamer.bot.handlers import _label_for

    msg = _FakeMessage(chat=_FakeChat(id=1, type="private"), from_user=_FakeUser("Ada Lovelace"))
    assert _label_for(msg) == "Ada Lovelace"  # type: ignore[arg-type]


def test_label_for_group_uses_title() -> None:
    from gamer.bot.handlers import _label_for

    msg = _FakeMessage(chat=_FakeChat(id=-100, type="supergroup", title="Streamers"))
    assert _label_for(msg) == "Streamers"  # type: ignore[arg-type]


def test_should_adopt_legacy_only_for_configured_operator_chats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gamer.config import get_settings

    monkeypatch.setenv("GAMER_TELEGRAM__DM_CHAT_ID", "111")
    monkeypatch.setenv("GAMER_TELEGRAM__GROUP_CHAT_ID", "-222")
    get_settings.cache_clear()

    from gamer.bot.handlers import _should_adopt_legacy

    assert _should_adopt_legacy("111") is True  # operator's DM
    assert _should_adopt_legacy("-222") is True  # operator's group
    assert _should_adopt_legacy("999") is False  # some other user
    assert _should_adopt_legacy("default") is False  # legacy never adopts itself


def test_copy_legacy_fields_deep_copies() -> None:
    from gamer.bot.handlers import _copy_legacy_fields
    from gamer.db.models import StreamerPref

    legacy = StreamerPref(
        key="default",
        liked_genres=["RPG"],
        blocked_genres=["Horror"],
        subscribed_genres=["Puzzle"],
        muted_game_ids=[7, 8],
        digest_enabled=False,
        profile_embedding=[0.1, 0.2],
    )
    row = StreamerPref(key="111")
    _copy_legacy_fields(legacy, row)

    assert row.subscribed_genres == ["Puzzle"]
    assert row.muted_game_ids == [7, 8]
    assert row.digest_enabled is False
    assert row.profile_embedding == [0.1, 0.2]
    # Deep copy: mutating the copy must not touch the legacy row's lists.
    row.subscribed_genres.append("Action")
    assert legacy.subscribed_genres == ["Puzzle"]
