import os
from collections.abc import AsyncGenerator

import pytest
from docker.errors import DockerException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from testcontainers.postgres import PostgresContainer

import app.core.db.models  # noqa
from app.core.db import Database
from app.core.db.models.base import Base

_PREPARED_DB_URLS: set[str] = set()


@pytest.fixture(scope="session")
def test_db_url() -> str | None:
    return os.getenv("TEST_DB__URL")


@pytest.fixture(scope="session")
def postgres(test_db_url):
    if test_db_url:
        yield None
        return

    try:
        with PostgresContainer("postgres:17") as pg:
            yield pg
    except DockerException as exc:
        pytest.skip(f"Docker is not available for DB tests: {exc}")


@pytest.fixture(scope="session")
def db_url(test_db_url, postgres) -> str:
    if test_db_url:
        return test_db_url
    return postgres.get_connection_url().replace("psycopg2", "asyncpg")


@pytest.fixture
async def db(db_url) -> AsyncGenerator[Database]:
    db = Database.from_url(db_url)

    if db_url not in _PREPARED_DB_URLS:
        async with db.engine.begin() as conn:
            for schema in ("core", "geo", "ext", "auth"):
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

        _PREPARED_DB_URLS.add(db_url)

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
