"""Fixtures for CRM tests that run the real app against the test database."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
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
