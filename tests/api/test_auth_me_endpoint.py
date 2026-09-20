"""`GET /auth/me`: what the calling token says about its bearer - without asking the database."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.v1.auth.me import get_me
from app.api.v1.common import ErrorResponse
from app.core.auth import AuthSettings, Scope, create_application_access_token, create_user_access_token
from app.core.auth.dependencies import get_auth_settings
from app.main import app

ME = "/api/v1/auth/me"
USER_ID = UUID("00000000-0000-0000-0000-0000000000a1")
PARTY_ID = UUID("00000000-0000-0000-0000-0000000000b2")
SESSION_ID = UUID("00000000-0000-0000-0000-0000000000c3")


async def test_me_reports_the_principal_type_the_client_and_the_sorted_scopes():
    async with _client() as client:
        response = await client.get(ME, headers=_auth_headers(Scope.CRM_WRITE, Scope.BOT_READ, Scope.CRM_READ))

    assert response.status_code == 200
    assert response.json() == {
        "principal_type": "application",
        "client_id": "swagger-operator",
        "scopes": ["bot:read", "crm:read", "crm:write"],
        "user_id": None,
        "party_id": None,
        "roles": [],
    }


async def test_me_reports_the_account_the_party_and_the_roles_of_a_user_token():
    async with _client() as client:
        response = await client.get(ME, headers=_user_auth_headers(roles=["tutor", "admin"]))

    assert response.status_code == 200
    assert response.json() == {
        "principal_type": "user",
        "client_id": "portal",
        "scopes": ["account:self", "crm:read:own"],
        "user_id": str(USER_ID),
        "party_id": str(PARTY_ID),
        "roles": ["admin", "tutor"],
    }


async def test_me_with_a_user_token_that_lost_its_party_is_the_401_envelope():
    async with _client() as client:
        response = await client.get(ME, headers=_broken_user_auth_headers(drop="party_id"))

    assert response.status_code == 401
    assert ErrorResponse.model_validate(response.json()).code == "unauthorized"


async def test_me_with_a_user_token_that_lost_its_session_is_the_401_envelope():
    async with _client() as client:
        response = await client.get(ME, headers=_broken_user_auth_headers(drop="sid"))

    assert response.status_code == 401
    assert ErrorResponse.model_validate(response.json()).code == "unauthorized"


async def test_me_with_a_user_token_whose_subject_is_not_its_principal_is_the_401_envelope():
    async with _client() as client:
        response = await client.get(ME, headers=_broken_user_auth_headers(subject="user:someone-else"))

    assert response.status_code == 401
    assert ErrorResponse.model_validate(response.json()).code == "unauthorized"


async def test_me_without_a_token_is_the_401_envelope():
    async with _client() as client:
        response = await client.get(ME)

    assert response.status_code == 401
    assert ErrorResponse.model_validate(response.json()).code == "unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_me_with_an_invalid_token_is_the_401_envelope():
    async with _client() as client:
        response = await client.get(ME, headers={"Authorization": "Bearer not-a-token"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid authentication credentials", "code": "unauthorized"}


def test_me_is_documented_as_a_guarded_operation_that_needs_no_scope():
    schema = app.openapi()
    operation = schema["paths"][ME]["get"]

    assert operation["operationId"] == "auth_get_me"
    assert [scopes for requirement in operation["security"] for scopes in requirement.values()] == [[]]
    # Any valid token passes, so the derived 401 is documented and a 403 is not.
    assert set(operation["responses"]) == {"200", "401"}

    properties = schema["components"]["schemas"]["MeResponse"]["properties"]
    assert set(properties) == {"principal_type", "client_id", "scopes", "user_id", "party_id", "roles"}
    assert all(prop.get("description") for prop in properties.values())


def test_me_depends_on_the_principal_only():
    """The route answers from the token alone: no session, no settings of its own."""
    assert list(inspect.signature(get_me).parameters) == ["principal"]


@asynccontextmanager
async def _client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_auth_settings] = lambda: _auth_settings()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth_headers(*scopes: Scope) -> dict[str, str]:
    token = create_application_access_token(
        _auth_settings(),
        principal_id=UUID("00000000-0000-0000-0000-000000000001"),
        client_id="swagger-operator",
        scopes=[str(scope) for scope in scopes],
    )
    return {"Authorization": f"Bearer {token.access_token}"}


def _user_auth_headers(*, roles: list[str]) -> dict[str, str]:
    token = create_user_access_token(
        _auth_settings(),
        principal_id=USER_ID,
        client_id="portal",
        party_id=PARTY_ID,
        session_id=SESSION_ID,
        scopes=[Scope.ACCOUNT_SELF, Scope.CRM_READ_OWN],
        roles=roles,
    )
    return {"Authorization": f"Bearer {token.access_token}"}


def _broken_user_auth_headers(*, drop: str | None = None, subject: str | None = None) -> dict[str, str]:
    """A correctly signed user token whose claims do not add up - forged, or issued by a past bug."""
    settings = _auth_settings()
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": subject or f"user:{USER_ID}",
        "principal_type": "user",
        "principal_id": str(USER_ID),
        "azp": "portal",
        "scope": "account:self",
        "party_id": str(PARTY_ID),
        "sid": str(SESSION_ID),
        "roles": [],
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": "00000000-0000-0000-0000-0000000000d4",
    }
    claims.pop(drop, None)
    token = jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
    return {"Authorization": f"Bearer {token}"}


def _auth_settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
