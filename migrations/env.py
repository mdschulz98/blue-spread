"""Alembic environment for the async (asyncpg) engine.

The URL comes from application settings, unless the caller passes a ready-made connection
(``config.attributes["connection"]``) or URL (``config.attributes["database_url"]``), which
the test-suite does.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401 - registers all models on Base.metadata
from app.db.base import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> URL:
    url = config.attributes.get("database_url")
    if url is not None:
        return url  # type: ignore[no-any-return]
    from app.core.config import get_settings

    return get_settings().database_url


def _configure(connection: Connection | None = None, **kwargs: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        **kwargs,  # type: ignore[arg-type]
    )


def run_migrations_offline() -> None:
    _configure(
        url=_database_url().render_as_string(hide_password=False),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
