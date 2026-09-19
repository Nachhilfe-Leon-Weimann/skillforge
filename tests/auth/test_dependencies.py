from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Principal, create_application_access_token, require_application, require_scopes
from app.core.auth.dependencies import get_auth_settings, get_current_principal

BotWritePrincipal = Annotated[Principal, require_scopes("bot:write")]
CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
ApplicationPrincipal = Annotated[Principal, Depends(require_application)]


async def test_get_current_principal_returns_principal_for_valid_token():
    settings = _settings()
    token = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:read"],
    )
    app = _app(settings)

    response = await _request(
        app,
        "GET",
        "/me",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal_type": "application",
        "client_id": "skillbot",
        "scopes": ["bot:read"],
    }


async def test_get_current_principal_rejects_missing_token():
    response = await _request(_app(_settings()), "GET", "/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_get_current_principal_rejects_invalid_token():
    response = await _request(_app(_settings()), "GET", "/me", headers={"Authorization": "Bearer not-a-token"})

    assert response.status_code == 401


async def test_require_scopes_accepts_token_with_required_scope():
    settings = _settings()
    token = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:write"],
    )
    app = _app(settings)

    response = await _request(
        app,
        "POST",
        "/write",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"client_id": "skillbot"}


async def test_require_scopes_rejects_token_without_required_scope():
    settings = _settings()
    token = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:read"],
    )
    app = _app(settings)

    response = await _request(
        app,
        "POST",
        "/write",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )

    assert response.status_code == 403


async def test_require_scopes_guards_a_route_from_the_decorator():
    settings = _settings()
    app = _app(settings)

    missing_token = await _request(app, "POST", "/guarded")
    wrong_scope = await _request(app, "POST", "/guarded", headers=_bearer(settings, scopes=["bot:read"]))
    granted = await _request(app, "POST", "/guarded", headers=_bearer(settings, scopes=["bot:write"]))

    assert missing_token.status_code == 401
    assert missing_token.headers["www-authenticate"] == 'Bearer scope="bot:write"'
    assert wrong_scope.status_code == 403
    assert granted.status_code == 200


def test_require_scopes_declares_the_scopes_in_openapi_for_both_positions():
    paths = _app(_settings()).openapi()["paths"]

    assert paths["/write"]["post"]["security"] == [{"OAuth2ClientCredentialsBearer": ["bot:write"]}]
    assert paths["/guarded"]["post"]["security"] == [{"OAuth2ClientCredentialsBearer": ["bot:write"]}]


async def test_require_application_rejects_non_application_principal():
    app = FastAPI()

    async def fake_principal() -> Principal:
        return Principal(
            principal_type="user",
            principal_id=uuid4(),
            subject="user:123",
            scopes=frozenset({"bot:read"}),
        )

    app.dependency_overrides[get_current_principal] = fake_principal

    @app.get("/application-only")
    async def application_only(principal: ApplicationPrincipal):
        return {"principal_type": principal.principal_type}

    response = await _request(app, "GET", "/application-only")

    assert response.status_code == 403


async def _request(
    app: FastAPI, method: str, path: str, *, raise_app_exceptions: bool = True, **kwargs: Any
) -> httpx.Response:
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _app(settings: AuthSettings) -> FastAPI:
    app = FastAPI()
    app.dependency_overrides[get_auth_settings] = lambda: settings

    @app.get("/me")
    async def me(principal: CurrentPrincipal):
        return {
            "principal_type": principal.principal_type,
            "client_id": principal.client_id,
            "scopes": sorted(principal.scopes),
        }

    @app.post("/write")
    async def write(principal: BotWritePrincipal):
        return {"client_id": principal.client_id}

    @app.post("/guarded", dependencies=[require_scopes("bot:write")])
    async def guarded():
        return {"ok": True}

    return app


def _bearer(settings: AuthSettings, *, scopes: list[str]) -> dict[str, str]:
    token = create_application_access_token(settings, principal_id=uuid4(), client_id="skillbot", scopes=scopes)
    return {"Authorization": f"Bearer {token.access_token}"}


def _settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
