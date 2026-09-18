"""Pins status, ``code`` and ``detail`` of every row of the CRM error catalog, per route.

Each case stubs the service function an endpoint calls, makes it raise the domain error with an
*internal* instance message, and asserts what a client sees. Every slice of the CRM API spec adds
its own rows.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.auth import AuthSettings, Scope, create_application_access_token
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session
from app.core.errors import DomainError
from app.main import app
from app.services.crm.errors import SubjectAlreadyExistsError, SubjectInUseError, SubjectNotFoundError

INTERNAL = "internal: row 7f3a"


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    stub: str
    """The service function the endpoint calls, as ``<module>.<name>`` under ``app.services.crm``."""
    json: dict[str, Any] | None = None


ENDPOINTS = {
    "create_subject": Endpoint("POST", "/subjects", "subjects.create_subject", json={"title": "Mathematics"}),
    "update_subject": Endpoint("PATCH", "/subjects/1", "subjects.update_subject", json={"title": "Mathematics"}),
    "delete_subject": Endpoint("DELETE", "/subjects/1", "subjects.delete_subject"),
}

# (endpoint, raised error, status, detail) - one row per cell of the spec's per-route table.
type Expectation = tuple[str, DomainError, int, str]

EXPECTATIONS: list[Expectation] = [
    ("create_subject", SubjectAlreadyExistsError(INTERNAL), 409, "Subject already exists"),
    ("update_subject", SubjectNotFoundError(INTERNAL), 404, "Subject not found"),
    ("update_subject", SubjectAlreadyExistsError(INTERNAL), 409, "Subject already exists"),
    ("delete_subject", SubjectNotFoundError(INTERNAL), 404, "Subject not found"),
    ("delete_subject", SubjectInUseError(INTERNAL), 409, "Subject is still assigned to students or tutors"),
]

# code -> (status, public message): the catalog itself, independent of any route.
CATALOG: dict[type[DomainError], tuple[str, int, str, bool]] = {
    SubjectNotFoundError: ("subject_not_found", 404, "Subject not found", False),
    SubjectAlreadyExistsError: ("subject_already_exists", 409, "Subject already exists", False),
    SubjectInUseError: ("subject_in_use", 409, "Subject is still assigned to students or tutors", False),
}


def _expectation_id(expectation: Expectation) -> str:
    name, error, _, _ = expectation
    return f"{name}-{type(error).__name__}[{error}]"


@pytest.mark.parametrize("expectation", EXPECTATIONS, ids=_expectation_id)
async def test_crm_domain_error_status_code_and_detail(expectation: Expectation, monkeypatch):
    name, error, status, detail = expectation
    endpoint = ENDPOINTS[name]
    monkeypatch.setattr(f"app.services.crm.{endpoint.stub}", _raises(error))

    async with _client() as client:
        response = await client.request(
            endpoint.method, f"/api/v1/crm{endpoint.path}", json=endpoint.json, headers=_auth_headers()
        )

    assert response.status_code == status
    assert response.json() == {"detail": detail, "code": CATALOG[type(error)][0]}
    assert INTERNAL not in response.text


@pytest.mark.parametrize("error_type", CATALOG, ids=lambda error_type: error_type.__name__)
def test_catalog_class_carries_its_code_message_and_exposure(error_type: type[DomainError]):
    from app.api.v1.common import status_for

    code, status, message, expose_message = CATALOG[error_type]

    assert (error_type.code, status_for(error_type), error_type.message, error_type.expose_message) == (
        code,
        status,
        message,
        expose_message,
    )


def test_every_catalog_class_is_pinned():
    from app.services.crm import errors

    defined = {
        error
        for error in vars(errors).values()
        if isinstance(error, type) and issubclass(error, DomainError) and error.__module__ == errors.__name__
    }

    assert defined == set(CATALOG)
    assert {type(error) for _, error, _, _ in EXPECTATIONS} == set(CATALOG)


def test_every_pinned_error_is_documented_on_its_route():
    for name, error, status, _ in EXPECTATIONS:
        responses = _operation(ENDPOINTS[name])["responses"]
        examples = responses[str(status)]["content"]["application/json"]["examples"]
        assert type(error).code in examples, f"{name}: {type(error).__name__}"


def test_no_route_documents_a_domain_error_that_is_not_pinned():
    pinned = {(name, type(error).code) for name, error, _, _ in EXPECTATIONS}
    catalog_codes = {code for code, *_ in CATALOG.values()}

    for name, endpoint in ENDPOINTS.items():
        for response in _operation(endpoint)["responses"].values():
            examples = response.get("content", {}).get("application/json", {}).get("examples", {})
            for code in set(examples) & catalog_codes:
                assert (name, code) in pinned, f"{name} documents {code} without a pinned expectation"


def test_every_crm_operation_that_documents_a_domain_error_is_in_the_table():
    catalog_codes = {code for code, *_ in CATALOG.values()}
    covered = {_operation(endpoint)["operationId"] for endpoint in ENDPOINTS.values()}

    for path, item in app.openapi()["paths"].items():
        if not path.startswith("/api/v1/crm/"):
            continue
        for operation in item.values():
            examples = {
                code
                for response in operation["responses"].values()
                for code in response.get("content", {}).get("application/json", {}).get("examples", {})
            }
            if examples & catalog_codes:
                assert operation["operationId"] in covered, operation["operationId"]


def _operation(endpoint: Endpoint) -> dict[str, Any]:
    """Find the OpenAPI operation whose path template matches the endpoint's concrete path."""
    concrete = f"/api/v1/crm{endpoint.path}"
    method = endpoint.method.lower()
    paths = app.openapi()["paths"]
    if concrete in paths and method in paths[concrete]:
        return paths[concrete][method]

    matches = [
        item[method]
        for template, item in paths.items()
        if method in item and re.fullmatch(re.sub(r"\{[^/]+\}", "[^/]+", template), concrete)
    ]
    assert len(matches) == 1, concrete
    return matches[0]


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


def _auth_headers() -> dict[str, str]:
    token = create_application_access_token(
        _auth_settings(),
        principal_id=UUID("00000000-0000-0000-0000-000000000001"),
        client_id="operator",
        scopes=[str(Scope.CRM_READ), str(Scope.CRM_WRITE)],
    )
    return {"Authorization": f"Bearer {token.access_token}"}


def _auth_settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
