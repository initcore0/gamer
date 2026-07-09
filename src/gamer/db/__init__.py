"""Database access layer (SQLAlchemy 2.0 async + pgvector)."""

from __future__ import annotations

from gamer.db.engine import (
    dispose_engine,
    get_engine,
    get_sessionmaker,
    session_scope,
)

__all__ = [
    "dispose_engine",
    "get_engine",
    "get_sessionmaker",
    "session_scope",
]
