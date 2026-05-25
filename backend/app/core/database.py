"""
Async SQLAlchemy engine + session factory + Base + FastAPI dependency.

[LEARN]
Pattern: "Unit of Work via per-request session".
Each HTTP request gets its own AsyncSession (opened by `get_session`, closed at
the end). The session is the transactional boundary: changes inside it are
either all committed or all rolled back together. Sharing one session across
requests would leak state and serialize writes — never do it.

Why async SQLAlchemy?
  Sync SQLAlchemy blocks the thread while Postgres is responding. FastAPI is
  single-threaded async by default. One slow query → the whole event loop
  stalls → all in-flight requests wait. Async SQLAlchemy uses asyncpg under
  the hood; while one query is in flight, the loop services other requests.

Read more:
  - https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
  - https://magicstack.github.io/asyncpg/current/
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base — every ORM model inherits from this."""


# [LEARN] Engine = connection pool + dialect. Create ONCE per process.
# Recreating an engine per request would defeat connection pooling and
# exhaust the DB's max_connections.
def _build_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.DATABASE_URL,
        echo=(settings.ENVIRONMENT == "dev"),  # log SQL in dev only
        pool_pre_ping=True,                    # cheap SELECT 1 before reuse — kills stale conns
        pool_size=10,
        max_overflow=20,
        future=True,
    )


engine: AsyncEngine = _build_engine()

# `async_sessionmaker` is the factory; calling it returns a fresh AsyncSession.
# expire_on_commit=False → ORM objects stay usable after commit (no auto-refresh).
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


# [LEARN] FastAPI dependency for per-request sessions.
# Usage in a route:
#   async def my_route(session: AsyncSession = Depends(get_session)): ...
# The `async with` block guarantees the session closes even if the handler raises.
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session bound to the request lifecycle."""
    async with SessionLocal() as session:
        yield session
