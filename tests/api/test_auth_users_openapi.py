"""Contract-level assertions over the user-account part of the generated OpenAPI document."""

import re
from typing import Any

import pytest

from app.main import app

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}
AUTH_OPERATION_ID = re.compile(r"auth_[a-z_]+")
SCHEMA_REF_PREFIX = "#/components/schemas/"

# The route map of the spec for this slice: method, path and operation ID are the contract.
ROUTE_MAP = {
    ("POST", "/users"): "auth_invite_user",
    ("GET", "/users"): "auth_list_users",
    ("GET", "/users/{user_id}"): "auth_get_user",
    ("PATCH", "/users/{user_id}"): "auth_update_user",
    ("PUT", "/users/{user_id}/roles/{role}"): "auth_add_user_role",
    ("DELETE", "/users/{user_id}/roles/{role}"): "auth_remove_user_role",
    ("POST", "/users/{user_id}/invitation"): "auth_issue_invitation",
    ("POST", "/users/{user_id}/password-reset"): "auth_issue_password_reset",
    ("DELETE", "/users/{user_id}/sessions"): "auth_revoke_user_sessions",
    ("POST", "/password/redeem"): "auth_redeem_password",
}
SUCCESS_STATUS = {
    "auth_invite_user": "201",
    "auth_issue_invitation": "201",
    "auth_issue_password_reset": "201",
    "auth_remove_user_role": "204",
    "auth_revoke_user_sessions": "204",
    "auth_redeem_password": "204",
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return app.openapi()


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    paths = {f"/api/v1/auth{path}" for _, path in ROUTE_MAP}
    return [
        (method.upper(), path, operation)
        for path, item in schema["paths"].items()
        if path in paths
        for method, operation in item.items()
        if method in HTTP_METHODS
    ]


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        own = {node["$ref"].removeprefix(SCHEMA_REF_PREFIX)} if isinstance(node.get("$ref"), str) else set()
        return own.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


def _schema_names(schema: dict[str, Any]) -> set[str]:
    """Every component schema reachable from a user-account operation."""
    schemas = schema["components"]["schemas"]
    pending = set().union(*(_refs(operation) for _, _, operation in _operations(schema)))
    reachable: set[str] = set()
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending |= _refs(schemas[name])
    return reachable


def test_the_routes_are_exactly_the_route_map_of_the_spec(schema: dict[str, Any]):
    actual = {
        (method, path.removeprefix("/api/v1/auth")): operation["operationId"]
        for method, path, operation in _operations(schema)
    }

    assert actual == ROUTE_MAP


def test_every_operation_id_is_auth_prefixed_snake_case(schema: dict[str, Any]):
    for method, path, operation in _operations(schema):
        assert AUTH_OPERATION_ID.fullmatch(operation["operationId"]), f"{method} {path}"
        assert operation["tags"] == ["auth"], f"{method} {path}"


def test_every_route_answers_with_the_status_of_the_route_map(schema: dict[str, Any]):
    for method, path, operation in _operations(schema):
        success = [status for status in operation["responses"] if status.startswith("2")]
        assert success == [SUCCESS_STATUS.get(operation["operationId"], "200")], f"{method} {path}"


def test_the_admin_routes_require_the_manage_scope_and_redeem_the_login_scope(schema: dict[str, Any]):
    for method, path, operation in _operations(schema):
        requirements = [scopes for requirement in operation["security"] for scopes in requirement.values()]
        required = [scope for scopes in requirements for scope in scopes]
        expected = ["auth:users:login"] if path.endswith("/password/redeem") else ["auth:users:manage"]
        assert required == expected, f"{method} {path}"


def test_every_schema_property_has_a_description(schema: dict[str, Any]):
    names = _schema_names(schema)

    assert {"UserAccountDetail", "InvitedUserAccount", "PasswordRedeemRequest"} <= names
    for name in sorted(names):
        for property_name, definition in schema["components"]["schemas"][name].get("properties", {}).items():
            assert definition.get("description", "").strip(), f"{name}.{property_name}"


def test_every_path_and_query_parameter_has_a_description(schema: dict[str, Any]):
    documented = 0
    for method, path, operation in _operations(schema):
        for parameter in operation.get("parameters", []):
            documented += 1
            assert parameter.get("description", "").strip(), f"{method} {path}: {parameter['name']}"

    assert documented


def test_every_path_parameter_has_examples(schema: dict[str, Any]):
    for method, path, operation in _operations(schema):
        for parameter in operation.get("parameters", []):
            if parameter["in"] == "path":
                assert parameter.get("examples") or parameter["schema"].get("examples"), (
                    f"{method} {path}: {parameter['name']}"
                )


def test_the_list_returns_the_generic_page(schema: dict[str, Any]):
    response = schema["paths"]["/api/v1/auth/users"]["get"]["responses"]["200"]

    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Page_UserAccountListItem_"
    }


def test_the_invitation_token_is_part_of_the_create_response_only(schema: dict[str, Any]):
    """The plaintext exists once: in the response that issues it (spec: security rules)."""
    carrying = {
        name for name in _schema_names(schema) if "token" in schema["components"]["schemas"][name].get("properties", {})
    }

    assert carrying == {"ActionTokenResponse", "PasswordRedeemRequest"}
    assert "invitation" in schema["components"]["schemas"]["InvitedUserAccount"]["properties"]
    assert "invitation" not in schema["components"]["schemas"]["UserAccountDetail"]["properties"]


def test_the_role_enum_of_the_path_lists_the_stored_roles_only(schema: dict[str, Any]):
    assert schema["components"]["schemas"]["UserAccountRoleName"]["enum"] == ["admin"]
    assert schema["components"]["schemas"]["Role"]["enum"] == ["student", "tutor", "guardian", "admin"]
