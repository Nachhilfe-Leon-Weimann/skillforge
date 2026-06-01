from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import DiscordUser, MemberRole
from app.main import app

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_read_discord_users_returns_users():
    session = _FakeSession([
        _discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor", active=False),
        _discord_user(discord_id=20, role=MemberRole.STUDENT, nick_name="Student"),
    ])

    with _overrides(session):
        async with _client() as client:
            response = await client.get("/api/v1/bot/users", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    users_by_id = {user["discord_id"]: user for user in response.json()}
    assert set(users_by_id) == {10, 20}
    assert _selected_fields(users_by_id[10]) == {
        "discord_id": 10,
        "role": "tutor",
        "nick_name": "Tutor",
        "active": False,
    }
    assert users_by_id[20]["role"] == "student"
    assert users_by_id[20]["active"] is True
    assert all(user["created_at"] for user in users_by_id.values())
    assert all(user["updated_at"] for user in users_by_id.values())


async def test_read_discord_user_returns_user():
    session = _FakeSession([
        _discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
    ])

    with _overrides(session):
        async with _client() as client:
            response = await client.get("/api/v1/bot/users/10", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    assert response.json()["discord_id"] == 10
    assert response.json()["role"] == "tutor"
    assert response.json()["nick_name"] == "Tutor"
    assert response.json()["active"] is True
    assert "created_at" in response.json()
    assert "updated_at" in response.json()


async def test_read_discord_user_returns_404_for_unknown_user():
    session = _FakeSession()

    with _overrides(session):
        async with _client() as client:
            response = await client.get("/api/v1/bot/users/999", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_read_discord_users_requires_bot_read_scope():
    session = _FakeSession()

    with _overrides(session):
        async with _client() as client:
            response = await client.get("/api/v1/bot/users", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


class _overrides:
    def __init__(self, session: _FakeSession):
        self.session = session

    def __enter__(self):
        async def override_db_session() -> AsyncIterator[_FakeSession]:
            yield self.session

        app.dependency_overrides[get_db_session] = override_db_session
        app.dependency_overrides[get_auth_settings] = lambda: _auth_settings()
        return self

    def __exit__(self, exc_type, exc, tb):
        app.dependency_overrides.clear()


@asynccontextmanager
async def _client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


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


def _discord_user(
    *,
    discord_id: int,
    role: MemberRole,
    nick_name: str,
    active: bool = True,
) -> DiscordUser:
    return DiscordUser(
        discord_id=discord_id,
        role=role,
        nick_name=nick_name,
        active=active,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakeSession:
    def __init__(self, users: list[DiscordUser] | None = None):
        self.users = users or []

    async def execute(self, statement):
        return _FakeResult(self.users)

    async def get(self, model, ident: int):
        if model is not DiscordUser:
            raise AssertionError("Unexpected model")

        return next((user for user in self.users if user.discord_id == ident), None)


class _FakeResult:
    def __init__(self, users: list[DiscordUser]):
        self.users = users

    def scalars(self):
        return self

    def all(self) -> list[DiscordUser]:
        return self.users


def _selected_fields(user: dict[str, object]) -> dict[str, object]:
    return {
        "discord_id": user["discord_id"],
        "role": user["role"],
        "nick_name": user["nick_name"],
        "active": user["active"],
    }
