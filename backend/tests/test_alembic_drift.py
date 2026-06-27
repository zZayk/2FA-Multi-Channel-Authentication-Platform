"""
Migration ↔ model drift guard.

Skipped by default; opt in with --run-integration.

[LEARN]
Pattern: "Schema drift detection".
Two sources describe the schema:
  1. The ORM models (`Base.metadata`) — what the app code expects.
  2. The Alembic migration chain — what actually gets applied to prod.
These MUST agree. If someone edits a model but forgets to add a migration
(or hand-writes a migration wrong), prod and code silently diverge.

This test applies all migrations to a throwaway database, then asks
Alembic's autogenerate engine to diff the live schema against
`Base.metadata`. A non-empty diff = drift = test fails, naming the
offending table/column.

Read more:
  - https://alembic.sqlalchemy.org/en/latest/api/autogenerate.html#alembic.autogenerate.compare_metadata
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import Base

# Register all models on Base.metadata.
import app.models.otp        # noqa: F401
import app.models.api_key    # noqa: F401
import app.models.blacklist  # noqa: F401

pytestmark = pytest.mark.integration

_BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/


def _swap_db(url: str, db_name: str) -> str:
    """Return `url` with its database/path replaced by `db_name`."""
    base, _, _old = url.rpartition("/")
    return f"{base}/{db_name}"


@pytest.fixture
async def drift_db_url(pg_url: str):
    """
    Create a fresh, empty database in the container for this test and
    return its async URL. Dropped afterwards.
    """
    db_name = f"drift_{uuid.uuid4().hex[:8]}"
    admin_url = pg_url  # connect to the default DB to issue CREATE DATABASE

    # CREATE/DROP DATABASE can't run inside a transaction → AUTOCOMMIT.
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin.dispose()

    try:
        yield _swap_db(pg_url, db_name)
    finally:
        admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        async with admin.connect() as conn:
            # Terminate stragglers, then drop.
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        await admin.dispose()


def _alembic_config(url: str) -> Config:
    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    # env.py now honours a pre-set URL instead of Settings.
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _diff(connection: Connection) -> list:
    mc = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": True},
    )
    return compare_metadata(mc, Base.metadata)


async def test_migrations_match_models(drift_db_url: str):
    # 1. Apply the full migration chain to the throwaway DB.
    #    Alembic's command API is sync; env.py drives the async engine via
    #    asyncio internally, so we call it directly (no extra thread needed).
    command.upgrade(_alembic_config(drift_db_url), "head")

    # 2. Diff the migrated schema against the ORM metadata.
    engine = create_async_engine(drift_db_url)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(_diff)
    finally:
        await engine.dispose()

    assert diffs == [], (
        "Schema drift between Alembic migrations and ORM models:\n"
        + "\n".join(repr(d) for d in diffs)
    )