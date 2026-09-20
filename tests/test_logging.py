import json
import logging
from typing import Annotated, Any
from uuid import uuid4

import httpx
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.v1.common import register_exception_handlers
from app.core.auth import (
    AuthSettings,
    Principal,
    create_application_access_token,
    create_user_access_token,
    require_scopes,
)
from app.core.auth.dependencies import get_auth_settings
from app.core.logging import LogFormat, LoggingSettings, LogLevel, configure_logging, register_request_logging

BotWritePrincipal = Annotated[Principal, require_scopes("bot:write")]
CrmReadOwnPrincipal = Annotated[Principal, require_scopes("crm:read:own")]


def test_logging_settings_default_to_skillforge_app_name():
    settings = LoggingSettings()

    assert settings.app_name == "skillforge"
    assert settings.file_path.name == "skillforge.jsonl"


async def test_request_logging_logs_not_found_with_request_id(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    register_request_logging(app)
    capsys.readouterr()

    response = await _request(app, "GET", "/missing")

    output = capsys.readouterr().out
    event = json.loads(output)

    assert response.status_code == 404
    assert response.headers["x-request-id"] == event["request_id"]
    assert event["event"] == "http_request_not_found"
    assert event["level"] == "warning"
    assert event["method"] == "GET"
    assert event["path"] == "/missing"
    assert event["status_code"] == 404


async def test_request_logging_includes_auth_context_for_missing_scopes(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    app.dependency_overrides[get_auth_settings] = _settings
    register_request_logging(app)

    @app.post("/write")
    async def write(principal: BotWritePrincipal):
        return {"client_id": principal.client_id}

    token = create_application_access_token(
        _settings(),
        principal_id=uuid4(),
        client_id="skillbot",
        scopes=["bot:read"],
    )
    capsys.readouterr()

    response = await _request(app, "POST", "/write", headers={"Authorization": f"Bearer {token.access_token}"})

    output = capsys.readouterr().out
    event = json.loads(output)

    assert response.status_code == 403
    assert event["event"] == "http_request_forbidden"
    assert event["auth_reason"] == "missing_scopes"
    assert event["client_id"] == "skillbot"
    assert event["required_scopes"] == ["bot:write"]
    assert event["missing_scopes"] == ["bot:write"]


async def test_request_logging_identifies_the_user_behind_a_request(capsys):
    """A user token says who called: the account and its party, never the session or a secret."""
    configure_logging(LoggingSettings(level=LogLevel.INFO, format=LogFormat.JSON))
    app = FastAPI()
    app.dependency_overrides[get_auth_settings] = _settings
    register_request_logging(app)

    @app.get("/parties")
    async def parties(principal: CrmReadOwnPrincipal):
        return {"party_id": str(principal.party_id)}

    user_id, party_id, session_id = uuid4(), uuid4(), uuid4()
    token = create_user_access_token(
        _settings(),
        principal_id=user_id,
        client_id="portal",
        party_id=party_id,
        session_id=session_id,
        scopes=["crm:read:own"],
        roles=["student"],
    )
    capsys.readouterr()

    response = await _request(app, "GET", "/parties", headers={"Authorization": f"Bearer {token.access_token}"})

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    completed = next(event for event in events if event["event"] == "http_request_completed")

    assert response.status_code == 200
    assert completed["principal_type"] == "user"
    assert completed["user_id"] == str(user_id)
    assert completed["party_id"] == str(party_id)
    assert str(session_id) not in json.dumps(completed)


async def test_request_logging_stays_silent_for_healthy_probe(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    register_request_logging(app)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    capsys.readouterr()

    response = await _request(app, "GET", "/health")

    assert response.status_code == 200
    assert capsys.readouterr().out == ""


async def test_request_logging_warns_on_unhealthy_probe(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    register_request_logging(app)

    @app.get("/health")
    async def health(response: Response):
        response.status_code = 503
        return {"status": "unhealthy"}

    capsys.readouterr()

    response = await _request(app, "GET", "/health")

    event = json.loads(capsys.readouterr().out)

    assert response.status_code == 503
    assert event["event"] == "http_probe_unhealthy"
    assert event["level"] == "warning"
    assert event["status_code"] == 503


async def test_request_logging_still_errors_on_non_probe_5xx(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    register_request_logging(app)

    @app.get("/boom")
    async def boom(response: Response):
        response.status_code = 500
        return {"detail": "nope"}

    capsys.readouterr()

    response = await _request(app, "GET", "/boom")

    event = json.loads(capsys.readouterr().out)

    assert response.status_code == 500
    assert event["event"] == "http_request_failed"
    assert event["level"] == "error"


async def test_request_logging_keeps_the_traceback_when_the_500_envelope_handles_the_exception(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    register_request_logging(app)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaputt")

    capsys.readouterr()

    response = await _request(app, "GET", "/boom", raise_app_exceptions=False)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    failed = [event for event in events if event["event"] == "http_request_failed"]

    assert response.json()["code"] == "internal_error"
    assert len(failed) == 1
    assert failed[0]["level"] == "error"
    assert failed[0]["status_code"] == 500
    assert "RuntimeError" in json.dumps(failed[0])
    assert "kaputt" in json.dumps(failed[0])


async def test_500_envelope_carries_the_request_id_of_the_logged_failure(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = _failing_app()
    capsys.readouterr()

    response = await _request(app, "GET", "/boom", raise_app_exceptions=False)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    failed = next(event for event in events if event["event"] == "http_request_failed")
    assert response.status_code == 500
    assert response.headers["x-request-id"] == failed["request_id"]


async def test_500_envelope_echoes_a_request_id_the_client_sent():
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))

    response = await _request(
        _failing_app(), "GET", "/boom", raise_app_exceptions=False, headers={"x-request-id": "trace-me-42"}
    )

    assert response.headers["x-request-id"] == "trace-me-42"


async def _request(
    app: FastAPI, method: str, path: str, *, raise_app_exceptions: bool = True, **kwargs: Any
) -> httpx.Response:
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _failing_app() -> FastAPI:
    app = FastAPI()
    register_request_logging(app)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaputt")

    return app


def test_configure_logging_disables_uvicorn_access_log():
    configure_logging(LoggingSettings(level=LogLevel.INFO, format=LogFormat.JSON))

    assert logging.getLogger("uvicorn").level == logging.NOTSET
    assert logging.getLogger("uvicorn.error").level == logging.NOTSET
    assert logging.getLogger("uvicorn.access").disabled is True


def _settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
