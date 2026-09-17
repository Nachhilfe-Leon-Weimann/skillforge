"""Contract-level assertions over the generated OpenAPI document."""

import re
from typing import Any

import pytest

from app.core.auth import Scope
from app.main import app

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
OPERATION_ID_PATTERN = re.compile(r"(auth|bot|crm|system)_[a-z0-9_]+")


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return app.openapi()


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def test_security_scheme_lists_every_scope_with_its_description(schema: dict[str, Any]):
    flows = [scheme["flows"] for scheme in schema["components"]["securitySchemes"].values() if "flows" in scheme]

    assert flows
    for flow in flows:
        assert flow["clientCredentials"]["scopes"] == {scope.value: scope.description for scope in Scope}


def test_every_operation_id_is_prefixed_with_its_domain_tag(schema: dict[str, Any]):
    operation_ids = [operation["operationId"] for _, _, operation in _operations(schema)]

    assert operation_ids
    for operation_id in operation_ids:
        assert OPERATION_ID_PATTERN.fullmatch(operation_id), operation_id


def test_operation_ids_are_unique(schema: dict[str, Any]):
    operation_ids = [operation["operationId"] for _, _, operation in _operations(schema)]

    assert len(operation_ids) == len(set(operation_ids))


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("POST", "/api/v1/auth/token", "auth_create_token"),
        ("GET", "/api/v1/bot/jobs", "bot_list_jobs"),
        ("GET", "/health", "system_health_check"),
        ("GET", "/health/live", "system_liveness_check"),
        ("GET", "/", "system_root"),
    ],
)
def test_operation_id_spot_checks(schema: dict[str, Any], method: str, path: str, expected: str):
    assert schema["paths"][path][method.lower()]["operationId"] == expected


def test_every_operation_has_exactly_one_documented_domain_tag(schema: dict[str, Any]):
    documented = {tag["name"]: tag.get("description", "") for tag in schema.get("tags", [])}

    for method, path, operation in _operations(schema):
        assert len(operation.get("tags", [])) == 1, f"{method} {path}"
        assert documented.get(operation["tags"][0], "").strip(), f"{method} {path}"
