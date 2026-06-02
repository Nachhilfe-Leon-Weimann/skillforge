import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.core.db.models  # noqa: F401  -- imported for its side effect of populating Base.metadata
from app.core.db.config import DatabaseSettings
from app.core.db.models.base import Base
from app.core.db.models.schemata import get_schemata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # DB__URL from the environment (or .env); an explicit env var overrides .env.
    return str(DatabaseSettings.from_env().url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
    )
    with context.begin_transaction():
        # Alembic does not manage schemas; create them within the migration transaction so
        # they (and the migration DDL) commit together. Doing this outside the transaction
        # leaves an uncommitted transaction that Alembic rolls back.
        for schema in sorted(get_schemata()):
            connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        context.run_migrations()


async def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
