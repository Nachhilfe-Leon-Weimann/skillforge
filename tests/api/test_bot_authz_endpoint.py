from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.main import app
from app.services.bot import PrincipalNotFoundError

PARTY_ID = UUID("22222222-2222-2222-2222-222222222222")


def _payload() -> dict[str, object]:
    return {"actor_discord_id": 42, "action_key": "student_stash", "target_party_id": str(PARTY_ID)}


async def test_check_returns_allowed_true(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_check(session, **kwargs):
        captured.update(kwargs)
        return True

    _patch(monkeypatch, "check_authorization", fake_check)

    async with _client() as client:
        response = await client.post("/api/v1/bot/authz/check", json=_payload(), headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    assert response.json() == {"allowed": True}
    assert captured == {"actor_discord_id": 42, "action_key": "student_stash", "target_party_id": PARTY_ID}


async def test_check_returns_allowed_false(monkeypatch):
    _patch(monkeypatch, "check_authorization", _returns(False))

    async with _client() as client:
        response = await client.post("/api/v1/bot/authz/check", json=_payload(), headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    assert response.json() == {"allowed": False}


async def test_check_requires_bot_read_scope():
    async with _client() as client:
        response = await client.post("/api/v1/bot/authz/check", json=_payload(), headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_check_returns_404_for_unknown_actor(monkeypatch):
    _patch(monkeypatch, "check_authorization", _raises(PrincipalNotFoundError()))

    async with _client() as client:
        response = await client.post("/api/v1/bot/authz/check", json=_payload(), headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404


# --- helpers ----------------------------------------------------------------


def _patch(monkeypatch, name: str, replacement) -> None:
    monkeypatch.setattr(f"app.api.v1.bot.authz.{name}", replacement)


def _returns(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _raises(error: Exception):
    async def _inner(*args, **kwargs):
        raise error

    return _inner


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
        client_id="skillbot",
        scopes=[str(scope) for scope in scopes],
    )
    return {"Authorization": f"Bearer {token.access_token}"}


def _auth_settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
