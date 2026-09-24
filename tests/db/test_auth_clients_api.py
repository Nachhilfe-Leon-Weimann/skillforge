"""The client grant routes against the test database: granted per mode, listed per mode, revoked per mode."""

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import ApplicationClientScopeGrant, AuthAuditLog, GrantMode
from app.main import app

_AUTH_SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))


@pytest.fixture
async def api(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An API client holding `auth:clients:manage` whose requests run on the test's ``session``.

    It creates the client `portal` first. Every request is wrapped in a SAVEPOINT that is rolled back
    when the request fails, which mirrors the request-scoped transaction of ``get_db_session``: a
    failed request writes nothing.
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
        scopes=[str(Scope.AUTH_CLIENTS_MANAGE)],
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver/api/v1/auth",
            headers={"Authorization": f"Bearer {token.access_token}"},
        ) as api_client:
            response = await api_client.post("/clients", json={"client_id": "portal", "name": "Portal"})
            assert response.status_code == 201
            yield api_client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.db
async def test_grants_are_made_listed_and_revoked_per_mode(api: AsyncClient, session: AsyncSession):
    await api.post("/clients/portal/scopes", json={"scopes": ["auth:users:login"], "mode": "application"})
    await api.post("/clients/portal/scopes", json={"scopes": ["crm:read", "account:self"], "mode": "delegated"})
    granted = await api.post("/clients/portal/scopes", json={"scopes": ["crm:read"], "mode": "application"})
    revoked = await api.delete("/clients/portal/scopes/delegated/crm:read")
    session.expunge_all()  # read the client back as a new request would: with nothing loaded
    detail = await api.get("/clients/portal")

    assert granted.status_code == 200
    assert (granted.json()["application_scopes"], granted.json()["delegated_scopes"]) == (
        ["auth:users:login", "crm:read"],
        ["account:self", "crm:read"],
    )
    assert revoked.status_code == 204
    assert (detail.json()["application_scopes"], detail.json()["delegated_scopes"]) == (
        ["auth:users:login", "crm:read"],
        ["account:self"],
    )
    assert await _scope_grant_details(session) == [
        "Granted scope auth:users:login in application mode to application client portal.",
        "Granted scope account:self in delegated mode to application client portal.",
        "Granted scope crm:read in delegated mode to application client portal.",
        "Granted scope crm:read in application mode to application client portal.",
        "Removed scope crm:read in delegated mode from application client portal.",
    ]


@pytest.mark.db
async def test_a_grant_is_revoked_in_its_own_mode_only(api: AsyncClient, session: AsyncSession):
    await api.post("/clients/portal/scopes", json={"scopes": ["crm:read"], "mode": "delegated"})

    response = await api.delete("/clients/portal/scopes/application/crm:read")

    assert response.status_code == 404
    assert response.json()["code"] == "application_client_scope_grant_not_found"
    assert await _grants(session) == {("crm:read", GrantMode.DELEGATED)}


@pytest.mark.db
async def test_granting_a_client_only_scope_as_delegated_is_invalid_scope_and_grants_nothing(
    api: AsyncClient, session: AsyncSession
):
    response = await api.post(
        "/clients/portal/scopes", json={"scopes": ["account:self", "auth:users:login"], "mode": "delegated"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid requested scope", "code": "invalid_scope"}
    assert await _grants(session) == set()
    assert await _scope_grant_details(session) == []


async def _grants(session: AsyncSession) -> set[tuple[str, GrantMode]]:
    rows = await session.execute(select(ApplicationClientScopeGrant.scope_key, ApplicationClientScopeGrant.mode))
    return set(rows.tuples())


async def _scope_grant_details(session: AsyncSession) -> list[str | None]:
    statement = select(AuthAuditLog.detail).where(
        AuthAuditLog.event_type.in_(["scope_grant.added", "scope_grant.removed"])
    )
    return list((await session.execute(statement)).scalars().all())
