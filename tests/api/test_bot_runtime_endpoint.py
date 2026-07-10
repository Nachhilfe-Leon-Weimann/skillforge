from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import (
    CommandEnvChannel,
    CommandEnvKind,
    ContactInfo,
    ContactInfoType,
    DiscordUser,
    MemberRole,
    Party,
    PartyType,
    Person,
    StudentChannelState,
    StudentWorkspace,
    TutorWorkspace,
)
from app.main import app
from app.services.bot import (
    CommandEnvNotFoundError,
    PrincipalNotFoundError,
    PrincipalView,
    StudentContextNotFoundError,
    StudentContextView,
    TutorContextNotFoundError,
    TutorContextView,
)

# --- principals -------------------------------------------------------------


async def test_read_principal_returns_principal_without_profile(monkeypatch):
    view = PrincipalView(
        user=_discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
        group_keys=["support"],
        permission_keys=["students.enable"],
        party=None,
    )
    _patch(monkeypatch, "get_principal_view", _returns(view))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/principals/10", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    body = response.json()
    assert body["discord_id"] == 10
    assert body["role"] == "tutor"
    assert body["display_name"] == "Tutor"
    assert body["active"] is True
    assert body["groups"] == ["support"]
    assert body["permissions"] == ["students.enable"]
    assert body["profile"] is None


async def test_read_principal_includes_operational_profile(monkeypatch):
    party_id = uuid4()
    view = PrincipalView(
        user=_discord_user(discord_id=10, role=MemberRole.STUDENT, nick_name="Student"),
        group_keys=[],
        permission_keys=[],
        party=_party_with_person(party_id),
    )
    _patch(monkeypatch, "get_principal_view", _returns(view))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/principals/10", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    profile = response.json()["profile"]
    assert profile["party_id"] == str(party_id)
    assert profile["person"] == {"firstname": "Max", "lastname": "Muster"}
    assert profile["contact_infos"] == [{"type": "email", "value": "max@example.com", "label": None}]
    assert profile["subjects"] == []
    assert profile["external_accounts"] == {"discord": None, "microsoft": None}


async def test_read_principal_returns_404(monkeypatch):
    _patch(monkeypatch, "get_principal_view", _raises(PrincipalNotFoundError()))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/principals/999", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_read_principal_requires_bot_read_scope():
    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/principals/10", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_runtime_requires_authentication():
    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/principals/10")

    assert response.status_code == 401


# --- tutor / student contexts ----------------------------------------------


async def test_read_tutor_context_returns_context(monkeypatch):
    view = TutorContextView(
        workspace=TutorWorkspace(
            guild_id=1,
            tutor_discord_id=10,
            category_channel_id=100,
            command_channel_id=101,
            student_channel_capacity=49,
        ),
        principal=PrincipalView(
            user=_discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
            group_keys=[],
            permission_keys=[],
            party=None,
        ),
    )
    _patch(monkeypatch, "get_tutor_context_view", _returns(view))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/tutors/1/10", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    body = response.json()
    assert body["guild_id"] == 1
    assert body["category_channel_id"] == 100
    assert body["command_channel_id"] == 101
    assert body["student_channel_capacity"] == 49
    assert body["principal"]["discord_id"] == 10


async def test_read_tutor_context_returns_404(monkeypatch):
    _patch(monkeypatch, "get_tutor_context_view", _raises(TutorContextNotFoundError()))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/tutors/1/10", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404


async def test_read_student_context_returns_context(monkeypatch):
    party_id = uuid4()
    view = StudentContextView(
        workspace=StudentWorkspace(
            guild_id=1,
            student_discord_id=20,
            tutor_discord_id=10,
            channel_id=300,
            channel_state=StudentChannelState.TUTOR_CATEGORY,
            current_parent_channel_id=100,
        ),
        principal=PrincipalView(
            user=_discord_user(discord_id=20, role=MemberRole.STUDENT, nick_name="Student"),
            group_keys=[],
            permission_keys=[],
            party=None,
        ),
        party_id=party_id,
    )
    _patch(monkeypatch, "get_student_context_view", _returns(view))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/students/1/20", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    body = response.json()
    assert body["party_id"] == str(party_id)
    assert body["tutor_discord_id"] == 10
    assert body["channel_id"] == 300
    assert body["channel_state"] == "tutor_category"
    assert body["current_parent_channel_id"] == 100
    assert body["principal"]["discord_id"] == 20


async def test_read_student_context_returns_404(monkeypatch):
    _patch(monkeypatch, "get_student_context_view", _raises(StudentContextNotFoundError()))

    async with _client() as client:
        response = await client.get("/api/v1/bot/runtime/students/1/20", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404


# --- command env resolve ----------------------------------------------------


async def test_resolve_command_env_returns_match(monkeypatch):
    command_env = CommandEnvChannel(
        guild_id=1,
        channel_id=100,
        kind=CommandEnvKind.TUTOR_CMD,
        owner_discord_id=10,
    )
    captured: dict[str, object] = {}

    async def fake_resolve(session, **kwargs):
        captured.update(kwargs)
        return command_env

    _patch(monkeypatch, "resolve_command_env", fake_resolve)

    async with _client() as client:
        response = await client.get(
            "/api/v1/bot/runtime/command-envs/resolve",
            params={"guild_id": 1, "channel_id": 100, "kind": "tutor_cmd", "owner_discord_id": 10},
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 200
    assert response.json() == {
        "guild_id": 1,
        "channel_id": 100,
        "kind": "tutor_cmd",
        "owner_discord_id": 10,
    }
    assert captured == {"guild_id": 1, "channel_id": 100, "kind": CommandEnvKind.TUTOR_CMD, "owner_discord_id": 10}


async def test_resolve_command_env_returns_404(monkeypatch):
    _patch(monkeypatch, "resolve_command_env", _raises(CommandEnvNotFoundError()))

    async with _client() as client:
        response = await client.get(
            "/api/v1/bot/runtime/command-envs/resolve",
            params={"guild_id": 1, "channel_id": 100, "kind": "tutor_cmd"},
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 404


# --- batch lookups ----------------------------------------------------------


async def test_read_principals_batch_returns_found_and_missing(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_views(session, discord_ids):
        captured["discord_ids"] = discord_ids
        return [
            PrincipalView(
                user=_discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
                group_keys=[],
                permission_keys=[],
                party=None,
            ),
            PrincipalView(
                user=_discord_user(discord_id=20, role=MemberRole.STUDENT, nick_name="Student"),
                group_keys=[],
                permission_keys=[],
                party=None,
            ),
        ]

    _patch(monkeypatch, "get_principal_views", fake_views)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/principals/batch",
            json={"discord_ids": [10, 20, 30]},
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["discord_id"] for item in body["found"]] == [10, 20]
    assert body["missing"] == [30]
    assert captured["discord_ids"] == [10, 20, 30]  # raw list passed through; service de-dups


async def test_read_principals_batch_dedups_requested_ids(monkeypatch):
    async def fake_views(session, discord_ids):
        return [
            PrincipalView(
                user=_discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
                group_keys=[],
                permission_keys=[],
                party=None,
            )
        ]

    _patch(monkeypatch, "get_principal_views", fake_views)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/principals/batch",
            json={"discord_ids": [10, 10, 20]},
            headers=_auth_headers(Scope.BOT_READ),
        )

    body = response.json()
    assert [item["discord_id"] for item in body["found"]] == [10]
    assert body["missing"] == [20]  # de-duplicated, request order


async def test_read_principals_batch_over_limit_returns_422():
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/principals/batch",
            json={"discord_ids": list(range(101))},
            headers=_auth_headers(Scope.BOT_READ),
        )
    assert response.status_code == 422


async def test_read_principals_batch_empty_returns_422():
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/principals/batch",
            json={"discord_ids": []},
            headers=_auth_headers(Scope.BOT_READ),
        )
    assert response.status_code == 422


async def test_read_principals_batch_requires_bot_read_scope():
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/principals/batch",
            json={"discord_ids": [10]},
            headers=_auth_headers(Scope.BOT_WRITE),
        )
    assert response.status_code == 403


async def test_batch_requires_authentication():
    async with _client() as client:
        response = await client.post("/api/v1/bot/runtime/principals/batch", json={"discord_ids": [10]})
    assert response.status_code == 401


async def test_read_tutor_contexts_batch_returns_found_and_missing(monkeypatch):
    async def fake_views(session, *, guild_id, tutor_discord_ids):
        return [
            TutorContextView(
                workspace=TutorWorkspace(
                    guild_id=guild_id,
                    tutor_discord_id=10,
                    category_channel_id=100,
                    command_channel_id=101,
                    student_channel_capacity=49,
                ),
                principal=PrincipalView(
                    user=_discord_user(discord_id=10, role=MemberRole.TUTOR, nick_name="Tutor"),
                    group_keys=[],
                    permission_keys=[],
                    party=None,
                ),
            )
        ]

    _patch(monkeypatch, "get_tutor_context_views", fake_views)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/tutors/1/batch",
            json={"discord_ids": [10, 11]},
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["principal"]["discord_id"] for item in body["found"]] == [10]
    assert body["found"][0]["guild_id"] == 1
    assert body["missing"] == [11]


async def test_read_student_contexts_batch_returns_found_and_missing(monkeypatch):
    party_id = uuid4()

    async def fake_views(session, *, guild_id, student_discord_ids):
        return [
            StudentContextView(
                workspace=StudentWorkspace(
                    guild_id=guild_id,
                    student_discord_id=20,
                    tutor_discord_id=10,
                    channel_id=300,
                    channel_state=StudentChannelState.TUTOR_CATEGORY,
                    current_parent_channel_id=100,
                ),
                principal=PrincipalView(
                    user=_discord_user(discord_id=20, role=MemberRole.STUDENT, nick_name="Student"),
                    group_keys=[],
                    permission_keys=[],
                    party=None,
                ),
                party_id=party_id,
            )
        ]

    _patch(monkeypatch, "get_student_context_views", fake_views)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/runtime/students/1/batch",
            json={"discord_ids": [20, 21]},
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["principal"]["discord_id"] for item in body["found"]] == [20]
    assert body["found"][0]["party_id"] == str(party_id)
    assert body["missing"] == [21]


# --- helpers ----------------------------------------------------------------


def _patch(monkeypatch, name: str, replacement) -> None:
    monkeypatch.setattr(f"app.api.v1.bot.runtime.{name}", replacement)


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
        client_id="skillbot",
        scopes=[str(scope) for scope in scopes],
    )
    return {"Authorization": f"Bearer {token.access_token}"}


def _auth_settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))


def _discord_user(*, discord_id: int, role: MemberRole, nick_name: str, active: bool = True) -> DiscordUser:
    return DiscordUser(discord_id=discord_id, role=role, nick_name=nick_name, active=active)


def _party_with_person(party_id: UUID) -> Party:
    party = Party(id=party_id, type=PartyType.PERSON)
    party.person = Person(firstname="Max", lastname="Muster")
    party.contact_infos = [ContactInfo(type=ContactInfoType.EMAIL, value="max@example.com")]
    party.outgoing_relations = []
    party.incoming_relations = []
    party.discord_account = None
    party.microsoft_account = None
    return party
