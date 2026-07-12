"""multi-user support — per-profile recommendations + prefs label

Revision ID: 0006_multiuser
Revises: 0005_subscribed_genres
Create Date: 2026-07-12

Turns the single-user system multi-user (MULTIUSER_PLAN.md):

* ``streamer_prefs.label`` (TEXT NULL) — a human display name for the profile
  (DM: the user's full name, group: the chat title). Legacy ``'default'`` keeps
  a NULL label.
* ``recommendations.pref_key`` (VARCHAR(64) NOT NULL, server_default ``'default'``)
  — the profile that owns each recommendation. Existing rows backfill to
  ``'default'`` via the server default, so per-profile cooldowns and the API's
  ``user_key`` filter see the legacy rows as the legacy profile's.
* index ``ix_rec_prefkey_created`` on ``(pref_key, created_at DESC)`` — the
  per-profile ``last_recommended`` cooldown query and the ``user_key``-filtered
  recommendation feed both seek on this.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_multiuser"
down_revision: str | None = "0005_subscribed_genres"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("streamer_prefs", sa.Column("label", sa.Text(), nullable=True))
    op.add_column(
        "recommendations",
        sa.Column(
            "pref_key",
            sa.String(length=64),
            nullable=False,
            server_default="default",
        ),
    )
    op.create_index(
        "ix_rec_prefkey_created",
        "recommendations",
        ["pref_key", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_rec_prefkey_created", table_name="recommendations")
    op.drop_column("recommendations", "pref_key")
    op.drop_column("streamer_prefs", "label")
