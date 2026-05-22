from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.v1.auth import get_issue_client_token
from app.core.auth import AuthSettings, CreatedAccessToken, InvalidClientCredentialsError, InvalidClientScopeError
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.main import app


def test_auth_token_endpoint_returns_access_token():
    captured: dict[str, object] = {}

    async def fake_create_token(
        session,
        settings,
        *,
        client_id,
        client_secret,
        requested_scopes,
    ):
        captured.update({
            "session": session,
            "settings": settings,
            "client_id": client_id,
            "client_secret": client_secret,
            "requested_scopes": requested_scopes,
        })
        return _token(scope="bot:read")

    with _overrides(fake_create_token):
        response = TestClient(app).post(
            "/api/v1/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "skillbot",
                "client_secret": "secret",
                "scope": "bot:read",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "encoded-token",
        "token_type": "bearer",
        "expires_in": 900,
        "scope": "bot:read",
    }
    assert captured["session"] == "session"
    assert captured["client_id"] == "skillbot"
    assert captured["client_secret"] == "secret"
    assert captured["requested_scopes"] == "bot:read"


def test_auth_token_endpoint_rejects_unsupported_grant_type():
    async def fake_create_token(*args, **kwargs):
        raise AssertionError("service should not be called")

    with _overrides(fake_create_token):
        response = TestClient(app).post(
            "/api/v1/auth/token",
            data={
                "grant_type": "password",
                "client_id": "skillbot",
                "client_secret": "secret",
            },
        )

    assert response.status_code == 400


def test_auth_token_endpoint_rejects_invalid_client_credentials():
    async def fake_create_token(*args, **kwargs):
        raise InvalidClientCredentialsError("invalid")

    with _overrides(fake_create_token):
        response = TestClient(app).post(
            "/api/v1/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "skillbot",
                "client_secret": "wrong",
            },
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_auth_token_endpoint_rejects_invalid_scope():
    async def fake_create_token(*args, **kwargs):
        raise InvalidClientScopeError("invalid scope")

    with _overrides(fake_create_token):
        response = TestClient(app).post(
            "/api/v1/auth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "skillbot",
                "client_secret": "secret",
                "scope": "users:write",
            },
        )

    assert response.status_code == 400


def test_auth_token_endpoint_requires_form_fields():
    async def fake_create_token(*args, **kwargs):
        raise AssertionError("service should not be called")

    with _overrides(fake_create_token):
        response = TestClient(app).post("/api/v1/auth/token", data={})

    assert response.status_code == 422


class _overrides:
    def __init__(self, fake_create_token):
        self.fake_create_token = fake_create_token

    def __enter__(self):
        app.dependency_overrides[get_db_session] = lambda: "session"
        app.dependency_overrides[get_auth_settings] = lambda: AuthSettings(
            secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes")
        )
        app.dependency_overrides[get_issue_client_token] = lambda: self.fake_create_token
        return self

    def __exit__(self, exc_type, exc, tb):
        app.dependency_overrides.clear()


def _token(*, scope: str) -> CreatedAccessToken:
    now = datetime.now(UTC)
    return CreatedAccessToken(
        access_token="encoded-token",
        token_type="bearer",
        expires_at=now + timedelta(minutes=15),
        expires_in=900,
        scope=scope,
    )
