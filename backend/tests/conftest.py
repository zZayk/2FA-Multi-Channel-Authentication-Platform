"""
Shared pytest fixtures.

[LEARN]
Pattern: "Test isolation via fixture-scoped resources".
conftest.py is auto-discovered by pytest. Anything defined here is available
to every test module below it — no imports needed. Fixtures with
`scope="session"` are built once per test run; `scope="function"` (default)
are rebuilt per test for full isolation.

Why we set env vars BEFORE importing app code:
  Settings() reads env at import time (via @lru_cache on get_settings).
  If we import the app first, then set SECRET_KEY, the cached settings
  still has the old missing-key state. Set env → then import.
"""

from __future__ import annotations

import os

# ----- Set deterministic test env BEFORE any app import ----------------------
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/app_test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/14")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/13")
os.environ.setdefault("BCRYPT_ROUNDS", "4")  # fast bcrypt in tests

import pytest  # noqa: E402  (must come after env setup above)
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def app():
    """One FastAPI app per test session — lifespan runs once."""
    return create_app()


@pytest.fixture
async def client(app):
    """HTTPX async client wired to the ASGI app — no real network."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac