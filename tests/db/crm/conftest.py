"""Fixtures for CRM tests that run the real app against the test database."""

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import Party
from app.main import app

_AUTH_SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An API client holding both CRM scopes whose requests run on the test's ``session``.

    Every request is wrapped in a SAVEPOINT that is rolled back when the request fails, which
    mirrors the request-scoped transaction of ``get_db_session``: a failed request writes nothing.
    """

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    app.dependency_overrides[get_db_session] = request_session
    app.dependency_overrides[get_auth_settings] = lambda: _AUTH_SETTINGS
    token = create_application_access_token(
        _AUTH_SETTINGS,
        principal_id=UUID("00000000-0000-0000-0000-000000000001"),
        client_id="operator",
        scopes=[str(Scope.CRM_READ), str(Scope.CRM_WRITE)],
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api/v1/crm",
            headers={"Authorization": f"Bearer {token.access_token}"},
        ) as api_client:
            yield api_client
    finally:
        app.dependency_overrides.clear()


LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def backdate(session: AsyncSession) -> Callable[..., Awaitable[None]]:
    """Set ``updated_at`` of the given parties to ``LONG_AGO`` and forget what the session holds.

    ``now()`` is the start of the transaction and a test runs in a single one, so "moved forward" is
    only observable against a timestamp in the past. Emptying the identity map makes the following
    write start like a request does: with nothing loaded.
    """

    async def _backdate(*party_ids: UUID) -> None:
        await session.flush()
        await session.execute(update(Party).where(Party.id.in_(party_ids)).values(updated_at=LONG_AGO))
        session.expunge_all()

    return _backdate


@pytest.fixture
def updated_at(session: AsyncSession) -> Callable[[UUID], Awaitable[datetime]]:
    """Read ``party.updated_at`` straight from the database."""

    async def _updated_at(party_id: UUID) -> datetime:
        value = await session.scalar(select(Party.updated_at).where(Party.id == party_id))
        assert value is not None
        return value

    return _updated_at


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
