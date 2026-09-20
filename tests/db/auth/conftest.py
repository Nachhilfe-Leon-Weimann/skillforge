"""Fixtures for auth tests that run the real app against the test database."""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import (
    ApplicationClient,
    ApplicationClientStatus,
    Company,
    Party,
    PartyType,
    Person,
    UserSession,
)
from app.main import app

AUTH_SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
OPERATOR_ID = UUID("00000000-0000-0000-0000-000000000001")
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


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
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An API client holding both user-management scopes whose requests run on the test's ``session``.

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
            headers=auth_headers(Scope.AUTH_USERS_MANAGE, Scope.AUTH_USERS_LOGIN),
        ) as api_client:
            yield api_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def make_person(session: AsyncSession) -> Callable[..., Awaitable[Party]]:
    """Create a person party straight through the models; the CRM API is not what is under test."""

    async def _make_person(firstname: str = "Anna", lastname: str = "Schmidt") -> Party:
        party = Party(id=uuid4(), type=PartyType.PERSON, person=Person(firstname=firstname, lastname=lastname))
        session.add(party)
        await session.flush()
        return party

    return _make_person


@pytest.fixture
def make_company(session: AsyncSession) -> Callable[..., Awaitable[Party]]:
    async def _make_company(name: str = "Musterfirma GmbH") -> Party:
        party = Party(id=uuid4(), type=PartyType.COMPANY, company=Company(name=name))
        session.add(party)
        await session.flush()
        return party

    return _make_company


@pytest.fixture
async def application_client(session: AsyncSession) -> ApplicationClient:
    """The client a session is opened for; ``user_session.application_client_id`` points at it."""
    client = ApplicationClient(
        id=uuid4(),
        client_id=f"portal-{uuid4().hex[:8]}",
        name="Portal",
        description=None,
        status=ApplicationClientStatus.ACTIVE,
    )
    session.add(client)
    await session.flush()
    return client


@pytest.fixture
def add_user_session(
    session: AsyncSession, application_client: ApplicationClient
) -> Callable[..., Awaitable[UserSession]]:
    """Insert a live session for an account.

    Nothing creates sessions before P0-6, so the tests that assert revocation insert them. The
    hashes are arbitrary unique strings, never anything that looks like a real refresh token.
    """

    async def _add_user_session(user_account_id: UUID) -> UserSession:
        user_session = UserSession(
            id=uuid4(),
            user_account_id=user_account_id,
            application_client_id=application_client.id,
            scope="account:self crm:read:own",
            refresh_token_hash=f"test-hash-{uuid4().hex}",
            expires_at=FAR_FUTURE,
        )
        session.add(user_session)
        await session.flush()
        return user_session

    return _add_user_session
