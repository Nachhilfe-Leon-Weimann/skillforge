from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Principal, create_application_access_token, require_application, require_scopes
from app.core.auth.dependencies import get_auth_settings, get_current_principal

BotWritePrincipal = Annotated[Principal, Depends(require_scopes("bot:write"))]
CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
ApplicationPrincipal = Annotated[Principal, Depends(require_application)]


def test_get_current_principal_returns_principal_for_valid_token():
    settings = _settings()
    token = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:read"],
    )
    app = _app(settings)

    response = TestClient(app).get(
        "/me",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "principal_type": "application",
        "client_id": "skillbot",
        "scopes": ["bot:read"],
    }


def test_get_current_principal_rejects_missing_token():
    response = TestClient(_app(_settings())).get("/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_get_current_principal_rejects_invalid_token():
    response = TestClient(_app(_settings())).get(
        "/me",
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 401


def test_require_scopes_accepts_token_with_required_scope():
    settings = _settings()
    token = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:write"],
    )
    app = _app(settings)

    response = TestClient(app).post(
        "/write",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"client_id": "skillbot"}


def test_require_scopes_rejects_token_without_required_scope():
    settings = _settings()
    token = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:read"],
    )
    app = _app(settings)

    response = TestClient(app).post(
        "/write",
        headers={"Authorization": f"Bearer {token.access_token}"},
    )

    assert response.status_code == 403


def test_require_application_rejects_non_application_principal():
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

    response = TestClient(app).get("/application-only")

    assert response.status_code == 403


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

    return app


def _settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
