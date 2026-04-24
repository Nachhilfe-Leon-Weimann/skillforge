from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.postgres import PostgresContainer

import skillcore.db.models  # noqa
from skillcore.db import Database, DatabaseSettings
from skillcore.db.models.base import Base


@pytest.fixture(scope="session")
def settings() -> DatabaseSettings | None:
    try:
        return DatabaseSettings.from_env()
    except Exception:
        return None


@pytest.fixture(scope="session")
def postgres(settings):
    if settings and settings.url:
        yield None
        return

    with PostgresContainer("postgres:17") as pg:
        yield pg


@pytest.fixture(scope="session")
def db_url(settings, postgres) -> str:
    if settings and settings.url:
        return str(settings.url)
    return postgres.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
async def db(db_url) -> AsyncGenerator[Database]:
    db = Database.from_url(db_url)

    async with db.engine.begin() as conn:
        for schema in ("core", "geo", "ext"):
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield db

    await db.dispose()


@pytest.fixture
async def session(db: Database) -> AsyncGenerator[AsyncSession]:
    async with db.engine.connect() as conn:
        trans = await conn.begin()
        session = db.session_factory(bind=conn)

        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
