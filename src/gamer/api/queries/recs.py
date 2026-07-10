"""Recommendation-feed query layer (UI_PLAN.md §3.4, §8 UI-M4).

``/recommendations`` is a chronological feed of the ``recommendations`` table,
newest first, grouped into "runs" (consecutive rows written within the same
minute — see :func:`group_runs`, a pure, unit-tested helper). Each row carries
its score, sent/unsent state, the aggregate feedback verdict counts, and the
score-component ``breakdown`` jsonb (rendered by the shared score-bars partial).

Rules (UI_PLAN.md §9): no SQL in routes; keyset pagination over
``(created_at DESC, id DESC)`` — never ``OFFSET``; feedback verdict counts come
from a single grouped outer join (no N+1); every value is a bound param.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select

from gamer.api.deps import decode_cursor, encode_cursor
from gamer.db import session_scope
from gamer.db.models import Feedback, FeedbackVerdict, Game, Recommendation

DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

# The verdicts we surface as emoji counts, in a stable render order.
_VERDICTS: tuple[str, ...] = tuple(v.value for v in FeedbackVerdict)


@dataclass(frozen=True, slots=True)
class RecRow:
    """One recommendation-feed row: rec + joined game + feedback + breakdown."""

    id: int
    game_id: int
    game_name: str
    score: float
    created_at: datetime
    sent_at: datetime | None
    feedback: dict[str, int]
    breakdown: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RecPage:
    """A page of recommendation rows plus the cursor for the next page (if any)."""

    rows: list[RecRow]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class RunGroup:
    """A group of consecutive recommendations sharing the same minute (§3.4)."""

    minute: datetime
    rows: list[RecRow]


def _clamp_limit(limit: int) -> int:
    if limit < 1:
        return DEFAULT_LIMIT
    return min(limit, _MAX_LIMIT)


def group_runs(rows: list[RecRow]) -> list[RunGroup]:
    """Group a newest-first row list into runs by ``created_at`` minute (pure).

    Consecutive rows whose ``created_at`` truncates to the same minute form one
    run group — the recommender emits a batch within seconds, so a shared minute
    is a good, cheap "same run" signal. Input order is preserved; a run boundary
    is any change in the truncated minute. Unit-tested DB-free.
    """
    groups: list[RunGroup] = []
    for row in rows:
        minute = row.created_at.replace(second=0, microsecond=0)
        if groups and groups[-1].minute == minute:
            groups[-1].rows.append(row)
        else:
            groups.append(RunGroup(minute=minute, rows=[row]))
    return groups


async def list_recommendations(
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> RecPage:
    """Keyset-paginated recommendation feed, newest first (UI_PLAN.md §3.4).

    Orders by ``(created_at DESC, id DESC)``; the cursor is the ISO ``created_at``
    plus ``id`` of the last row. Feedback verdict counts are aggregated in a
    single grouped subquery LEFT JOINed per rec (no N+1). Talks to the DB →
    integration-only.
    """
    limit = _clamp_limit(limit)

    # Per-verdict feedback counts, aggregated once, then LEFT JOINed. Using a
    # crosstab of conditional counts keeps it to one grouped subquery per rec.
    fb = (
        select(
            Feedback.rec_id.label("rec_id"),
            *[
                func.count().filter(Feedback.verdict == v.value).label(f"fb_{v.value}")
                for v in FeedbackVerdict
            ],
        )
        .group_by(Feedback.rec_id)
        .subquery()
    )

    stmt = (
        select(
            Recommendation.id,
            Recommendation.game_id,
            Game.name,
            Recommendation.score,
            Recommendation.created_at,
            Recommendation.sent_at,
            Recommendation.breakdown,
            *[getattr(fb.c, f"fb_{v}") for v in _VERDICTS],
        )
        .select_from(Recommendation)
        .join(Game, Game.id == Recommendation.game_id)
        .outerjoin(fb, fb.c.rec_id == Recommendation.id)
    )

    seek = decode_cursor(cursor, (str, int))
    if seek is not None:
        last_iso, last_id = seek
        last_dt = _parse_dt(last_iso)
        if last_dt is not None:
            stmt = stmt.where(
                or_(
                    Recommendation.created_at < last_dt,
                    and_(
                        Recommendation.created_at == last_dt,
                        Recommendation.id < int(last_id),
                    ),
                )
            )

    stmt = stmt.order_by(Recommendation.created_at.desc(), Recommendation.id.desc()).limit(
        limit + 1
    )

    async with session_scope() as session:
        result = (await session.execute(stmt)).all()

    has_more = len(result) > limit
    page = result[:limit]
    rows = [_to_row(rt) for rt in page]

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor((last.created_at.isoformat(), last.id))
    return RecPage(rows=rows, next_cursor=next_cursor)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _to_row(rt: Any) -> RecRow:
    (
        rec_id,
        game_id,
        name,
        score,
        created_at,
        sent_at,
        breakdown,
        *counts,
    ) = rt
    feedback = {v: int(c or 0) for v, c in zip(_VERDICTS, counts, strict=True)}
    return RecRow(
        id=int(rec_id),
        game_id=int(game_id),
        game_name=str(name),
        score=float(score),
        created_at=created_at,
        sent_at=sent_at,
        feedback=feedback,
        breakdown=dict(breakdown or {}),
    )
