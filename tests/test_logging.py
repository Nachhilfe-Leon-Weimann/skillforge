import json
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Principal, create_application_access_token, require_scopes
from app.core.auth.dependencies import get_auth_settings
from app.core.logging import LogFormat, LoggingSettings, LogLevel, configure_logging, register_request_logging

BotWritePrincipal = Annotated[Principal, Depends(require_scopes("bot:write"))]


def test_logging_settings_default_to_skillforge_app_name():
    settings = LoggingSettings()

    assert settings.app_name == "skillforge"
    assert settings.file_path.name == "skillforge.jsonl"


def test_request_logging_logs_not_found_with_request_id(capsys):
    configure_logging(LoggingSettings(level=LogLevel.WARNING, format=LogFormat.JSON))
    app = FastAPI()
    register_request_logging(app)
    capsys.readouterr()

    response = TestClient(app).get("/missing")

    output = capsys.readouterr().out
    event = json.loads(output)

    assert response.status_code == 404
    assert response.headers["x-request-id"] == event["request_id"]
    assert event["event"] == "http_request_not_found"
    assert event["level"] == "warning"
    assert event["method"] == "GET"
    assert event["path"] == "/missing"
    assert event["status_code"] == 404


def test_request_logging_includes_auth_context_for_missing_scopes(capsys):
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

    response = TestClient(app).post("/write", headers={"Authorization": f"Bearer {token.access_token}"})

    output = capsys.readouterr().out
    event = json.loads(output)

    assert response.status_code == 403
    assert event["event"] == "http_request_forbidden"
    assert event["auth_reason"] == "missing_scopes"
    assert event["client_id"] == "skillbot"
    assert event["required_scopes"] == ["bot:write"]
    assert event["missing_scopes"] == ["bot:write"]


def test_configure_logging_disables_uvicorn_access_log():
    configure_logging(LoggingSettings(level=LogLevel.INFO, format=LogFormat.JSON))

    assert logging.getLogger("uvicorn").level == logging.NOTSET
    assert logging.getLogger("uvicorn.error").level == logging.NOTSET
    assert logging.getLogger("uvicorn.access").disabled is True


def _settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
