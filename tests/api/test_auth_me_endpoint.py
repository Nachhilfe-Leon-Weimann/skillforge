"""`GET /auth/me`: what the calling token says about its bearer - without asking the database."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.v1.auth.me import get_me
from app.api.v1.common import ErrorResponse
from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.main import app

ME = "/api/v1/auth/me"


async def test_me_reports_the_principal_type_the_client_and_the_sorted_scopes():
    async with _client() as client:
        response = await client.get(ME, headers=_auth_headers(Scope.CRM_WRITE, Scope.BOT_READ, Scope.CRM_READ))

    assert response.status_code == 200
    assert response.json() == {
        "principal_type": "application",
        "client_id": "swagger-operator",
        "scopes": ["bot:read", "crm:read", "crm:write"],
    }


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
    assert set(properties) == {"principal_type", "client_id", "scopes"}
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


def _auth_settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
