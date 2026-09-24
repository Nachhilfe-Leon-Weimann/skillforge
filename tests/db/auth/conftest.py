"""Fixtures for auth tests that run the real app, or the auth services, against the test database."""

from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.audit import AuditEventType
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import ApplicationClientScopeGrant, AuthAuditLog, GrantMode
from app.main import app

AUTH_SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
OPERATOR_ID = UUID("00000000-0000-0000-0000-000000000001")


def auth_headers(*scopes: Scope) -> dict[str, str]:
    """A bearer token of an application client holding ``scopes``."""
    token = create_application_access_token(
        AUTH_SETTINGS,
        principal_id=OPERATOR_ID,
        client_id="operator",
        scopes=[str(scope) for scope in scopes],
    )
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.fixture
def auth_settings() -> AuthSettings:
    """The settings the ``client`` fixture signs and verifies its tokens with."""
    return AUTH_SETTINGS


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An API client holding `auth:clients:manage` whose requests run on the test's ``session``.

    Every request is wrapped in a SAVEPOINT that is rolled back when the request fails, which
    mirrors the request-scoped transaction of ``get_db_session``: a failed request writes nothing.
    """

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    app.dependency_overrides[get_db_session] = request_session
    app.dependency_overrides[get_auth_settings] = lambda: AUTH_SETTINGS
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api/v1/auth",
            headers=auth_headers(Scope.AUTH_CLIENTS_MANAGE),
        ) as api_client:
            yield api_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def grants(session: AsyncSession) -> Callable[[], Awaitable[set[tuple[str, GrantMode]]]]:
    """Read every client scope grant as ``(scope_key, mode)`` straight from the database."""

    async def _grants() -> set[tuple[str, GrantMode]]:
        rows = await session.execute(select(ApplicationClientScopeGrant.scope_key, ApplicationClientScopeGrant.mode))
        return set(rows.tuples())

    return _grants


@pytest.fixture
def scope_grant_details(session: AsyncSession) -> Callable[[], Awaitable[Counter[str | None]]]:
    """Read the details of the grant and revocation audit entries.

    A ``Counter``: entries written in one transaction share their timestamp, so the log has no order to compare.
    """

    async def _scope_grant_details() -> Counter[str | None]:
        statement = select(AuthAuditLog.detail).where(
            AuthAuditLog.event_type.in_([AuditEventType.SCOPE_GRANT_ADDED, AuditEventType.SCOPE_GRANT_REMOVED])
        )
        return Counter((await session.execute(statement)).scalars())

    return _scope_grant_details
