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
from app.services.bot import OperationNotFoundError, OperationNotPendingError

# --- get by id --------------------------------------------------------------


async def test_get_operation_returns_full_operation(monkeypatch):
    op = _operation(plan={"step": "reserve"})
    _patch(monkeypatch, "get_operation", _returns(op))

    async with _client() as client:
        response = await client.get(f"/api/v1/bot/operations/{op.operation_id}", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == str(op.operation_id)
    assert body["kind"] == "student_stash"
    assert body["status"] == "prepared"
    assert body["guild_id"] == 1
    assert body["subject_discord_id"] == 100
    assert body["plan"] == {"step": "reserve"}
    assert "expires_at" in body
    assert "cancelled_at" in body


async def test_get_operation_returns_404(monkeypatch):
    _patch(monkeypatch, "get_operation", _raises(OperationNotFoundError()))

    async with _client() as client:
        response = await client.get(f"/api/v1/bot/operations/{uuid4()}", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_get_operation_requires_bot_read_scope():
    async with _client() as client:
        response = await client.get(f"/api/v1/bot/operations/{uuid4()}", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_operations_requires_authentication():
    async with _client() as client:
        response = await client.get(f"/api/v1/bot/operations/{uuid4()}")

    assert response.status_code == 401


# --- filtered list ----------------------------------------------------------


async def test_list_operations_returns_page(monkeypatch):
    op = _operation()
    captured: dict[str, object] = {}

    async def fake_list(session, **kwargs):
        captured.update(kwargs)
        return [op], 1

    _patch(monkeypatch, "list_operations", fake_list)

    async with _client() as client:
        response = await client.get(
            "/api/v1/bot/operations",
            params={
                "guild_id": 1,
                "subject_discord_id": 100,
                "status": "prepared",
                "kind": "student_stash",
                "limit": 10,
                "offset": 5,
            },
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 10
    assert body["offset"] == 5
    assert len(body["items"]) == 1
    assert body["items"][0]["operation_id"] == str(op.operation_id)
    # The summary list item omits the heavy plan; it is only on the by-id detail.
    assert "plan" not in body["items"][0]
    assert captured == {
        "guild_id": 1,
        "subject_discord_id": 100,
        "status": OperationStatus.PREPARED,
        "kind": OperationKind.STUDENT_STASH,
        "limit": 10,
        "offset": 5,
    }


async def test_list_operations_defaults_to_no_filters(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_list(session, **kwargs):
        captured.update(kwargs)
        return [], 0

    _patch(monkeypatch, "list_operations", fake_list)

    async with _client() as client:
        response = await client.get("/api/v1/bot/operations", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert captured == {
        "guild_id": None,
        "subject_discord_id": None,
        "status": None,
        "kind": None,
        "limit": 50,
        "offset": 0,
    }


async def test_list_operations_requires_bot_read_scope():
    async with _client() as client:
        response = await client.get("/api/v1/bot/operations", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_list_operations_requires_authentication():
    async with _client() as client:
        response = await client.get("/api/v1/bot/operations")

    assert response.status_code == 401


async def test_list_operations_rejects_out_of_range_params():
    async with _client() as client:
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"guild_id": -1}):
            response = await client.get("/api/v1/bot/operations", params=params, headers=_auth_headers(Scope.BOT_READ))
            assert response.status_code == 422, params


# --- cancel -----------------------------------------------------------------


async def test_cancel_operation_returns_cancelled(monkeypatch):
    op = _operation(status=OperationStatus.CANCELLED, cancelled_at=datetime(2026, 1, 2, tzinfo=UTC))
    _patch(monkeypatch, "cancel_operation", _returns(op))

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/operations/{op.operation_id}/cancel", headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 200
    body = response.json()
    assert body["operation_id"] == str(op.operation_id)
    assert body["status"] == "cancelled"
    assert body["cancelled_at"] is not None
    # The lean cancel response omits the heavy plan.
    assert "plan" not in body


async def test_cancel_operation_maps_not_found_to_404(monkeypatch):
    _patch(monkeypatch, "cancel_operation", _raises(OperationNotFoundError()))

    async with _client() as client:
        response = await client.post(f"/api/v1/bot/operations/{uuid4()}/cancel", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 404


async def test_cancel_operation_maps_not_pending_to_409(monkeypatch):
    _patch(monkeypatch, "cancel_operation", _raises(OperationNotPendingError("Operation is not in a prepared state")))

    async with _client() as client:
        response = await client.post(f"/api/v1/bot/operations/{uuid4()}/cancel", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 409


async def test_cancel_operation_requires_bot_write_scope():
    async with _client() as client:
        response = await client.post(f"/api/v1/bot/operations/{uuid4()}/cancel", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 403


async def test_cancel_operation_requires_authentication():
    async with _client() as client:
        response = await client.post(f"/api/v1/bot/operations/{uuid4()}/cancel")

    assert response.status_code == 401


# --- helpers ----------------------------------------------------------------


def _operation(**overrides) -> Operation:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    defaults: dict[str, object] = {
        "operation_id": uuid4(),
        "kind": OperationKind.STUDENT_STASH,
        "status": OperationStatus.PREPARED,
        "guild_id": 1,
        "subject_discord_id": 100,
        "tutor_discord_id": None,
        "reserved_archive_category_channel_id": None,
        "plan": {"step": "reserve"},
        "expires_at": now,
        "committed_at": None,
        "cancelled_at": None,
        "failed_at": None,
        "last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Operation(**defaults)


def _patch(monkeypatch, name: str, replacement) -> None:
    monkeypatch.setattr(f"app.api.v1.bot.operations.{name}", replacement)


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
