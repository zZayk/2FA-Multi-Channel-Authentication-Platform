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

Integration tests:
  Tests marked `@pytest.mark.integration` start a real Postgres container
  via testcontainers. They're SKIPPED by default to keep `pytest -q`
  fast in environments without a Docker daemon. Opt in:
      pytest --run-integration
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

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402


# =============================================================================
# Integration marker — opt-in via --run-integration
# =============================================================================

def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests (spin up real Postgres via testcontainers).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return  # nothing to skip
    skip_integration = pytest.mark.skip(
        reason="needs --run-integration (testcontainers/Docker required)"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# =============================================================================
# App fixtures (unit / smoke / schema tests)
# =============================================================================

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def app():
    """One FastAPI app per test session — lifespan runs once."""
    return create_app()


@pytest.fixture
async def client(app):
    """HTTPX async client wired to the ASGI app — no real network, no auth override."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def auth_client(app):
    """
    Like `client`, but with `require_api_key` overridden to return a stub key.

    [LEARN] Pattern: "Dependency override for tests".
    FastAPI lets us replace any `Depends`-injected callable via
    `app.dependency_overrides`. Here we bypass the DB-backed auth check so
    schema-validation tests can reach body validation (422) without a real
    Postgres or a real key. The override is removed in teardown so it can't
    leak into other tests.
    """
    import uuid

    from app.core.security import require_api_key
    from app.models.api_key import APIKey

    stub = APIKey(
        id=uuid.uuid4(),
        name="test-stub",
        key_hash="0" * 64,
        key_prefix="l2t_test",
    )

    async def _override() -> APIKey:
        return stub

    app.dependency_overrides[require_api_key] = _override
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(require_api_key, None)


# =============================================================================
# DB fixtures (integration tests only)
# =============================================================================
# Imported lazily — testcontainers + Docker not needed for unit-only runs.

@pytest.fixture(scope="session")
def pg_container():
    """Session-scoped Postgres 15 container."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:15-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_url(pg_container) -> str:
    """asyncpg DSN for the running container."""
    # testcontainers gives a sync driver URL; swap to asyncpg.
    raw = pg_container.get_connection_url()
    return raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


@pytest.fixture(scope="session")
async def db_engine(pg_url: str):
    """
    Async engine bound to the container. Creates schema once via
    Base.metadata.create_all — bypasses Alembic for test-loop speed.
    A separate test verifies migrations build the same schema (Week 4).
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.database import Base
    # Import all model modules so their tables register on Base.metadata.
    import app.models.otp      # noqa: F401
    import app.models.api_key  # noqa: F401

    engine = create_async_engine(pg_url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """
    Per-test AsyncSession. Truncates `otps` between tests so each test
    starts on a clean slate without paying schema-rebuild cost.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async with db_engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE otps RESTART IDENTITY CASCADE"))

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session