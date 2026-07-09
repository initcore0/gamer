from __future__ import annotations

from datetime import date

from gamer.notify.base import Channel
from gamer.notify.digest import build_digest
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


def test_mover_math() -> None:
    m = Mover(game_id=1, name="X", platform_app_id=1, latest=150, baseline=100)
    assert m.delta == 50
    assert m.pct == 50.0
    zero = Mover(game_id=2, name="Y", platform_app_id=2, latest=10, baseline=0)
    assert zero.pct is None  # guards divide-by-zero baseline
