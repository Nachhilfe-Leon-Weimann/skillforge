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
from app.core.db.models import Job, JobStatus
from app.main import app
from app.services.bot import (
    JobKindCountsView,
    JobNotClaimedError,
    JobNotFoundError,
    JobQueueSummaryView,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_claim_jobs_returns_claimed_jobs(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_claim(session, **kwargs):
        captured.update(kwargs)
        return [_job(kind="activate_tutor", payload={"tutor_discord_id": 7}, status=JobStatus.CLAIMED, attempt=1)]

    _patch(monkeypatch, "claim_jobs", fake_claim)

    async with _client() as client:
        response = await client.post(
            "/api/v1/bot/jobs/claim",
            json={"kinds": ["activate_tutor"], "limit": 5, "worker": "shard-1"},
            headers=_auth_headers(Scope.BOT_WRITE),
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["kind"] == "activate_tutor"
    assert body[0]["payload"] == {"tutor_discord_id": 7}
    assert body[0]["attempt"] == 1
    assert body[0]["claimed_at"] is not None
    assert captured == {"kinds": ["activate_tutor"], "limit": 5, "worker": "shard-1"}


async def test_claim_jobs_requires_bot_write_scope():
    async with _client() as client:
        response = await client.post("/api/v1/bot/jobs/claim", json={}, headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 403


async def test_jobs_require_authentication():
    async with _client() as client:
        response = await client.post("/api/v1/bot/jobs/claim", json={})

    assert response.status_code == 401


async def test_complete_job_returns_state(monkeypatch):
    job = _job(kind="activate_tutor", status=JobStatus.COMPLETED, attempt=1, completed_at=_NOW)
    _patch(monkeypatch, "complete_job", _returns(job))

    async with _client() as client:
        response = await client.post(f"/api/v1/bot/jobs/{job.job_id}/complete", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job.job_id)
    assert body["status"] == "completed"


async def test_complete_job_returns_404(monkeypatch):
    _patch(monkeypatch, "complete_job", _raises(JobNotFoundError()))

    async with _client() as client:
        response = await client.post(f"/api/v1/bot/jobs/{uuid4()}/complete", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 404


async def test_complete_job_returns_409_when_not_claimed(monkeypatch):
    _patch(monkeypatch, "complete_job", _raises(JobNotClaimedError()))

    async with _client() as client:
        response = await client.post(f"/api/v1/bot/jobs/{uuid4()}/complete", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 409


async def test_fail_job_returns_state_and_passes_args(monkeypatch):
    job = _job(kind="activate_tutor", status=JobStatus.FAILED, attempt=1, failed_at=_NOW, last_error="boom")
    captured: dict[str, object] = {}

    async def fake_fail(session, **kwargs):
        captured.update(kwargs)
        return job

    _patch(monkeypatch, "fail_job", fake_fail)

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/jobs/{job.job_id}/fail",
            json={"error": "boom", "retry": True},
            headers=_auth_headers(Scope.BOT_WRITE),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert captured == {"job_id": job.job_id, "error": "boom", "retry": True}


async def test_fail_job_returns_409_when_not_claimed(monkeypatch):
    _patch(monkeypatch, "fail_job", _raises(JobNotClaimedError()))

    async with _client() as client:
        response = await client.post(
            f"/api/v1/bot/jobs/{uuid4()}/fail", json={}, headers=_auth_headers(Scope.BOT_WRITE)
        )

    assert response.status_code == 409


# --- read plane: get by id / list / summary ---------------------------------


async def test_read_job_returns_detail(monkeypatch):
    job = _job(
        kind="activate_tutor",
        status=JobStatus.CLAIMED,
        attempt=1,
        payload={"tutor_discord_id": 7},
        claimed_by="shard-1",
    )
    _patch(monkeypatch, "get_job", _returns(job))

    async with _client() as client:
        response = await client.get(f"/api/v1/bot/jobs/{job.job_id}", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job.job_id)
    assert body["kind"] == "activate_tutor"
    assert body["status"] == "claimed"
    assert body["payload"] == {"tutor_discord_id": 7}
    assert body["max_attempts"] == 5
    assert body["claimed_by"] == "shard-1"


async def test_read_job_returns_404(monkeypatch):
    _patch(monkeypatch, "get_job", _raises(JobNotFoundError()))

    async with _client() as client:
        response = await client.get(f"/api/v1/bot/jobs/{uuid4()}", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 404
    assert "detail" in response.json()


async def test_read_job_requires_bot_read_scope():
    async with _client() as client:
        response = await client.get(f"/api/v1/bot/jobs/{uuid4()}", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_list_jobs_returns_page(monkeypatch):
    job = _job(kind="activate_tutor", status=JobStatus.FAILED, attempt=2, payload={"x": 1}, last_error="boom")
    captured: dict[str, object] = {}

    async def fake_list(session, **kwargs):
        captured.update(kwargs)
        return [job], 1

    _patch(monkeypatch, "list_jobs", fake_list)

    async with _client() as client:
        response = await client.get(
            "/api/v1/bot/jobs",
            params={"status": "failed", "kind": "activate_tutor", "limit": 10, "offset": 5},
            headers=_auth_headers(Scope.BOT_READ),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["limit"] == 10
    assert body["offset"] == 5
    assert len(body["items"]) == 1
    assert body["items"][0]["job_id"] == str(job.job_id)
    assert body["items"][0]["status"] == "failed"
    # The list item is a status view; the payload is only on the by-id detail.
    assert "payload" not in body["items"][0]
    assert captured == {"status": JobStatus.FAILED, "kind": "activate_tutor", "limit": 10, "offset": 5}


async def test_list_jobs_defaults_to_no_filters(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_list(session, **kwargs):
        captured.update(kwargs)
        return [], 0

    _patch(monkeypatch, "list_jobs", fake_list)

    async with _client() as client:
        response = await client.get("/api/v1/bot/jobs", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert captured == {"status": None, "kind": None, "limit": 50, "offset": 0}


async def test_list_jobs_requires_bot_read_scope():
    async with _client() as client:
        response = await client.get("/api/v1/bot/jobs", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_job_summary_returns_funnel(monkeypatch):
    view = JobQueueSummaryView(
        total=4,
        by_status={
            JobStatus.PENDING: 2,
            JobStatus.CLAIMED: 0,
            JobStatus.COMPLETED: 1,
            JobStatus.FAILED: 1,
        },
        by_kind=[
            JobKindCountsView(
                kind="activate_tutor",
                counts={
                    JobStatus.PENDING: 2,
                    JobStatus.CLAIMED: 0,
                    JobStatus.COMPLETED: 1,
                    JobStatus.FAILED: 0,
                },
            )
        ],
    )
    _patch(monkeypatch, "get_job_queue_summary", _returns(view))

    async with _client() as client:
        response = await client.get("/api/v1/bot/jobs/summary", headers=_auth_headers(Scope.BOT_READ))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 4
    assert body["by_status"] == {"pending": 2, "claimed": 0, "completed": 1, "failed": 1}
    assert body["by_kind"] == [
        {"kind": "activate_tutor", "counts": {"pending": 2, "claimed": 0, "completed": 1, "failed": 0}}
    ]


async def test_job_summary_requires_bot_read_scope():
    async with _client() as client:
        response = await client.get("/api/v1/bot/jobs/summary", headers=_auth_headers(Scope.BOT_WRITE))

    assert response.status_code == 403


async def test_read_endpoints_require_authentication():
    async with _client() as client:
        for path in (f"/api/v1/bot/jobs/{uuid4()}", "/api/v1/bot/jobs/summary", "/api/v1/bot/jobs"):
            response = await client.get(path)
            assert response.status_code == 401, path


async def test_list_jobs_rejects_out_of_range_params():
    async with _client() as client:
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            response = await client.get("/api/v1/bot/jobs", params=params, headers=_auth_headers(Scope.BOT_READ))
            assert response.status_code == 422, params


# --- helpers ----------------------------------------------------------------


def _patch(monkeypatch, name: str, replacement) -> None:
    monkeypatch.setattr(f"app.api.v1.bot.jobs.{name}", replacement)


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


def _job(
    *,
    kind: str,
    status: JobStatus,
    attempt: int,
    payload: dict | None = None,
    claimed_by: str | None = None,
    completed_at: datetime | None = None,
    failed_at: datetime | None = None,
    last_error: str | None = None,
) -> Job:
    return Job(
        job_id=uuid4(),
        kind=kind,
        payload=payload or {},
        status=status,
        attempt=attempt,
        max_attempts=5,
        available_at=_NOW,
        claimed_at=_NOW,
        claimed_by=claimed_by,
        completed_at=completed_at,
        failed_at=failed_at,
        last_error=last_error,
        created_at=_NOW,
        updated_at=_NOW,
    )
