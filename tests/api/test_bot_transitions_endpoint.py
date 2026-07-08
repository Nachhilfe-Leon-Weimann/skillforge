from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.db.models import Operation, OperationKind, OperationStatus
from app.main import app
from app.services.bot import (
    OperationNotFoundError,
    OperationNotPendingError,
    TransitionConflictError,
    TransitionValidationError,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


# --- tutor activation -------------------------------------------------------


async def test_prepare_tutor_activation_returns_operation(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_prepare(session, **kwargs):
        captured.update(kwargs)
        return _operation(kind=OperationKind.TUTOR_ACTIVATE, plan={"action": "create_tutor_workspace"})

    _patch(monkeypatch, "tutors", "prepare_tutor_activation", fake_prepare)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/tutors/activations/prepare",
            json={"guild_id": 1, "tutor_discord_id": 10},
            headers=_auth_headers(Scope.BOT_WRITE),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "tutor_activate"
    assert body["plan"] == {"action": "create_tutor_workspace"}
    assert "operation_id" in body and "expires_at" in body
    assert captured == {"guild_id": 1, "tutor_discord_id": 10}


async def test_prepare_tutor_activation_requires_bot_write():
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/tutors/activations/prepare",
            json={"guild_id": 1, "tutor_discord_id": 10},
            headers=_auth_headers(Scope.BOT_READ),
        )
    assert response.status_code == 403


async def test_prepare_tutor_activation_conflict_409(monkeypatch):
    _patch(monkeypatch, "tutors", "prepare_tutor_activation", _raises(TransitionConflictError("exists")))
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/tutors/activations/prepare",
            json={"guild_id": 1, "tutor_discord_id": 10},
            headers=_auth_headers(Scope.BOT_WRITE),
        )
    assert response.status_code == 409


async def test_prepare_tutor_activation_validation_422(monkeypatch):
    _patch(monkeypatch, "tutors", "prepare_tutor_activation", _raises(TransitionValidationError("not a tutor")))
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/tutors/activations/prepare",
            json={"guild_id": 1, "tutor_discord_id": 10},
            headers=_auth_headers(Scope.BOT_WRITE),
        )
    assert response.status_code == 422


async def test_commit_tutor_activation_returns_committed(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_commit(session, **kwargs):
        captured.update(kwargs)
        return _operation(kind=OperationKind.TUTOR_ACTIVATE, status=OperationStatus.COMMITTED, committed_at=_NOW)

    _patch(monkeypatch, "tutors", "commit_tutor_activation", fake_commit)
    op_id = uuid4()

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/tutors/activations/{op_id}/commit",
            json={"category_channel_id": 100, "command_channel_id": 101},
            headers=_auth_headers(Scope.BOT_WRITE),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "committed"
    assert captured == {"operation_id": op_id, "category_channel_id": 100, "command_channel_id": 101}


async def test_commit_tutor_activation_operation_not_found_404(monkeypatch):
    _patch(monkeypatch, "tutors", "commit_tutor_activation", _raises(OperationNotFoundError()))
    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/tutors/activations/{uuid4()}/commit",
            json={"category_channel_id": 100, "command_channel_id": 101},
            headers=_auth_headers(Scope.BOT_WRITE),
        )
    assert response.status_code == 404


async def test_commit_tutor_activation_not_pending_409(monkeypatch):
    _patch(monkeypatch, "tutors", "commit_tutor_activation", _raises(OperationNotPendingError("expired")))
    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/tutors/activations/{uuid4()}/commit",
            json={"category_channel_id": 100, "command_channel_id": 101},
            headers=_auth_headers(Scope.BOT_WRITE),
        )
    assert response.status_code == 409


# --- student stash / pop (path-based) ---------------------------------------


async def test_prepare_student_stash_returns_operation(monkeypatch):
    async def fake_prepare(session, **kwargs):
        assert kwargs == {"guild_id": 1, "student_discord_id": 20}
        return _operation(kind=OperationKind.STUDENT_STASH, plan={"action": "stash", "archive_no": 1})

    _patch(monkeypatch, "students", "prepare_student_stash", fake_prepare)

    async with _client() as client:
        response = await client.post("/api/v1/bot/students/1/20/stash/prepare", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 200
    assert response.json()["kind"] == "student_stash"


async def test_commit_student_pop_returns_committed(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_commit(session, **kwargs):
        captured.update(kwargs)
        return _operation(kind=OperationKind.STUDENT_POP, status=OperationStatus.COMMITTED, committed_at=_NOW)

    _patch(monkeypatch, "students", "commit_student_pop", fake_commit)
    op_id = uuid4()

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/students/1/20/pop/{op_id}/commit", headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 200
    assert response.json()["status"] == "committed"
    assert captured == {"operation_id": op_id}


async def test_prepare_student_activation_uses_static_route(monkeypatch):
    # Ensures /students/activations/prepare is matched as a static route, not /{guild_id}/...
    async def fake_prepare(session, **kwargs):
        return _operation(kind=OperationKind.STUDENT_ACTIVATE, plan={})

    _patch(monkeypatch, "students", "prepare_student_activation", fake_prepare)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/students/activations/prepare",
            json={"guild_id": 1, "student_discord_id": 20, "tutor_discord_id": 10},
            headers=_auth_headers(Scope.BOT_WRITE),
        )

    assert response.status_code == 200
    assert response.json()["kind"] == "student_activate"


# --- student / tutor deactivation (path-based) ------------------------------


async def test_prepare_student_deactivation_returns_operation(monkeypatch):
    async def fake_prepare(session, **kwargs):
        assert kwargs == {"guild_id": 1, "student_discord_id": 20}
        return _operation(kind=OperationKind.STUDENT_DEACTIVATE, plan={"action": "delete_student_channel"})

    _patch(monkeypatch, "students", "prepare_student_deactivation", fake_prepare)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/students/1/20/deactivate/prepare", headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 200
    assert response.json()["kind"] == "student_deactivate"


async def test_prepare_student_deactivation_requires_bot_write():
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/students/1/20/deactivate/prepare", headers=_auth_headers(Scope.BOT_READ)
        )
    assert response.status_code == 403


async def test_commit_student_deactivation_returns_committed(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_commit(session, **kwargs):
        captured.update(kwargs)
        return _operation(kind=OperationKind.STUDENT_DEACTIVATE, status=OperationStatus.COMMITTED, committed_at=_NOW)

    _patch(monkeypatch, "students", "commit_student_deactivation", fake_commit)
    op_id = uuid4()

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/students/1/20/deactivate/{op_id}/commit", headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 200
    assert response.json()["status"] == "committed"
    assert captured == {"operation_id": op_id}


async def test_prepare_tutor_deactivation_returns_operation(monkeypatch):
    async def fake_prepare(session, **kwargs):
        assert kwargs == {"guild_id": 1, "tutor_discord_id": 10}
        return _operation(kind=OperationKind.TUTOR_DEACTIVATE, plan={"action": "delete_tutor_workspace"})

    _patch(monkeypatch, "tutors", "prepare_tutor_deactivation", fake_prepare)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/tutors/1/10/deactivate/prepare", headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 200
    assert response.json()["kind"] == "tutor_deactivate"


async def test_prepare_tutor_deactivation_refuses_while_occupied_409(monkeypatch):
    _patch(monkeypatch, "tutors", "prepare_tutor_deactivation", _raises(TransitionConflictError("still occupied")))
    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/tutors/1/10/deactivate/prepare", headers=_auth_headers(Scope.BOT_WRITE)
        )
    assert response.status_code == 409


async def test_commit_tutor_deactivation_returns_committed(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_commit(session, **kwargs):
        captured.update(kwargs)
        return _operation(kind=OperationKind.TUTOR_DEACTIVATE, status=OperationStatus.COMMITTED, committed_at=_NOW)

    _patch(monkeypatch, "tutors", "commit_tutor_deactivation", fake_commit)
    op_id = uuid4()

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/tutors/1/10/deactivate/{op_id}/commit", headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 200
    assert response.json()["status"] == "committed"
    assert captured == {"operation_id": op_id}


# --- helpers ----------------------------------------------------------------


def _patch(monkeypatch, module: str, name: str, replacement) -> None:
    monkeypatch.setattr(f"app.api.v1.bot.{module}.{name}", replacement)


def _raises(error: Exception):
    async def _inner(*args, **kwargs):
        raise error

    return _inner


def _operation(
    *,
    kind: OperationKind,
    status: OperationStatus = OperationStatus.PREPARED,
    plan: dict | None = None,
    committed_at: datetime | None = None,
) -> Operation:
    return Operation(
        operation_id=uuid4(),
        kind=kind,
        status=status,
        guild_id=1,
        subject_discord_id=10,
        plan=plan or {},
        expires_at=_NOW,
        committed_at=committed_at,
    )


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
