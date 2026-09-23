"""Runtime guards of the user-account routes: 401 without a token, 403 without the right principal.

The cases run over every `/auth/users` operation of the OpenAPI document, so a route added later
is covered without touching this file.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.v1.common import ErrorResponse
from app.core.auth import AuthSettings, Principal, Scope, create_application_access_token
from app.core.auth import dependencies as auth_dependencies
from app.core.auth.dependencies import get_auth_settings, get_current_principal
from app.core.auth.services import action_tokens as action_tokens_service
from app.core.db.dependencies import get_db_session
from app.main import app

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}
REDEEM_PATH = "/api/v1/auth/password/redeem"
PATH_VALUES = {
    "user_id": "00000000-0000-0000-0000-0000000000dd",
    "role": "admin",
}
BODIES: dict[str, dict[str, object]] = {
    "POST /api/v1/auth/users": {"party_id": str(uuid4()), "email": "anna@example.org"},
    "PATCH /api/v1/auth/users/00000000-0000-0000-0000-0000000000dd": {"email": "anna@example.org"},
    f"POST {REDEEM_PATH}": {"token": "sf_ua_whatever", "new_password": "correct horse battery"},
}


def _user_operations() -> list[tuple[str, str]]:
    return [
        (method.upper(), path.format(**PATH_VALUES))
        for path, item in app.openapi()["paths"].items()
        if path.startswith("/api/v1/auth/users")
        for method in item
        if method in HTTP_METHODS
    ]


def test_the_operation_table_covers_the_whole_admin_surface():
    assert len(_user_operations()) == 9
    assert ("POST", "/api/v1/auth/users") in _user_operations()


@pytest.mark.parametrize(("method", "path"), _user_operations())
async def test_a_user_route_answers_401_without_a_token(method: str, path: str):
    async with _client() as client:
        response = await client.request(method, path, json=BODIES.get(f"{method} {path}"))

    assert response.status_code == 401
    assert ErrorResponse.model_validate(response.json()).code == "unauthorized"
    assert response.headers["www-authenticate"] == 'Bearer scope="auth:users:manage"'


@pytest.mark.parametrize(("method", "path"), _user_operations())
async def test_a_user_route_answers_403_without_the_manage_scope(method: str, path: str):
    async with _client() as client:
        response = await client.request(
            method, path, json=BODIES.get(f"{method} {path}"), headers=_auth_headers(Scope.AUTH_USERS_LOGIN)
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "Not enough permissions", "code": "forbidden"}


async def test_the_redeem_route_answers_403_without_the_login_scope():
    async with _client() as client:
        response = await client.post(
            REDEEM_PATH, json=BODIES[f"POST {REDEEM_PATH}"], headers=_auth_headers(Scope.AUTH_USERS_MANAGE)
        )

    assert response.status_code == 403


async def test_the_redeem_route_answers_403_for_a_user_principal_whatever_its_scopes():
    """It logs a user in on their behalf, so it is a client's route (spec: route map)."""

    async def user_principal() -> Principal:
        return Principal(
            principal_type="user",
            principal_id=uuid4(),
            subject=f"user:{uuid4()}",
            scopes=frozenset({str(Scope.AUTH_USERS_LOGIN), str(Scope.AUTH_USERS_MANAGE)}),
        )

    async with _client() as client:
        app.dependency_overrides[get_current_principal] = user_principal
        response = await client.post(REDEEM_PATH, json=BODIES[f"POST {REDEEM_PATH}"])

    assert response.status_code == 403
    assert response.json() == {"detail": "Application principal required", "code": "forbidden"}


async def test_the_redeem_route_validates_the_token_once(monkeypatch):
    """One guard declaration: the scope and the principal type are checked on a single validation."""
    validate = auth_dependencies.validate_access_token
    validations: list[str] = []

    def counting(token: str, settings: AuthSettings) -> Principal:
        validations.append(token)
        return validate(token, settings)

    async def redeemed(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(auth_dependencies, "validate_access_token", counting)
    monkeypatch.setattr(action_tokens_service, "redeem_action_token", redeemed)
    async with _client() as client:
        response = await client.post(
            REDEEM_PATH, json=BODIES[f"POST {REDEEM_PATH}"], headers=_auth_headers(Scope.AUTH_USERS_LOGIN)
        )

    assert response.status_code == 204
    assert len(validations) == 1


async def _override_db_session() -> AsyncIterator[object]:
    yield object()


@asynccontextmanager
async def _client() -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_db_session] = _override_db_session
    app.dependency_overrides[get_auth_settings] = _auth_settings
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _auth_headers(*scopes: Scope) -> dict[str, str]:
    token = create_application_access_token(
        _auth_settings(),
        principal_id=UUID("00000000-0000-0000-0000-000000000001"),
        client_id="operator",
        scopes=[str(scope) for scope in scopes],
    )
    return {"Authorization": f"Bearer {token.access_token}"}


def _auth_settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
