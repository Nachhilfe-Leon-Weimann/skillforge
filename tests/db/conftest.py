import os
from collections.abc import AsyncGenerator, Iterator

import pytest
from docker.errors import DockerException
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from testcontainers.postgres import PostgresContainer

import app.core.db.models  # noqa
from app.core.db import Database
from app.core.db.models.base import Base
from app.core.db.models.schemata import get_schemata

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
            for schema in get_schemata():
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


@pytest.fixture
def statements(session: AsyncSession) -> Iterator[list[str]]:
    """Every SQL statement the test's connection executes, in order; ``clear()`` it before measuring."""
    connection = session.bind
    assert isinstance(connection, AsyncConnection)
    recorded: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        recorded.append(" ".join(statement.split()))

    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        yield recorded
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)
