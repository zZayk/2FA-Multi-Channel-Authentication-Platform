"""
Alembic env — async-aware.

[LEARN]
Alembic was originally sync. To run with an async engine we:
  1. Build the async engine ourselves.
  2. Use `connection.run_sync(do_migrations)` to bridge to Alembic's sync API.
This pattern is the official recipe in Alembic's "Asynchronous environment"
cookbook entry.

Why we import all models here:
  Alembic discovers metadata via `target_metadata = Base.metadata`. If a model
  module is never imported, its tables are absent from metadata, so autogenerate
  can't see them and they don't get created. Importing the package's models
  module triggers all the table-class definitions.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context

# --- App imports -------------------------------------------------------------
from app.core.config import get_settings  # noqa: E402
from app.core.database import Base        # noqa: E402

# Register all models on Base.metadata by importing the package.
# Add new model modules to app/models/__init__.py for autodiscovery.
import app.models.otp  # noqa: F401,E402  (side-effect import)

# --- Standard Alembic setup --------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject the URL from Settings (single source of truth).
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)

target_metadata = Base.metadata


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,            # detect column-type changes
        compare_server_default=True,  # detect server default changes
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine: AsyncEngine = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        future=True,
        poolclass=None,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    """Generate SQL without a DB connection — useful for CI dry-runs."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the live DB via async engine."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()