"""Contract-level assertions over the CRM part of the generated OpenAPI document."""

import re
from typing import Any

import pytest

from app.core.auth import Scope
from app.main import app

HTTP_METHODS = {"get", "put", "post", "delete", "patch"}
CRM_PREFIX = "/api/v1/crm/"
CRM_OPERATION_ID = re.compile(r"crm_[a-z_]+")
SCHEMA_REF_PREFIX = "#/components/schemas/"


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return app.openapi()


def _crm_operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, operation)
        for path, item in schema["paths"].items()
        if path.startswith(CRM_PREFIX)
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


def _crm_schema_names(schema: dict[str, Any]) -> set[str]:
    """Every component schema reachable from a CRM operation."""
    schemas = schema["components"]["schemas"]
    pending = set().union(*(_refs(operation) for _, _, operation in _crm_operations(schema)))
    reachable: set[str] = set()
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending |= _refs(schemas[name])
    return reachable


def test_both_crm_scopes_are_listed_with_their_description(schema: dict[str, Any]):
    flows = [scheme["flows"] for scheme in schema["components"]["securitySchemes"].values() if "flows" in scheme]

    assert flows
    for flow in flows:
        scopes = flow["clientCredentials"]["scopes"]
        assert scopes["crm:read"] == Scope.CRM_READ.description
        assert scopes["crm:write"] == Scope.CRM_WRITE.description
        assert scopes["crm:read"].strip() and scopes["crm:write"].strip()


def test_every_crm_operation_id_is_crm_prefixed_snake_case(schema: dict[str, Any]):
    operations = _crm_operations(schema)

    assert operations
    for method, path, operation in operations:
        assert CRM_OPERATION_ID.fullmatch(operation["operationId"]), f"{method} {path}"
        assert operation["tags"] == ["crm"], f"{method} {path}"


def test_crm_reads_require_the_read_scope_and_everything_else_the_write_scope(schema: dict[str, Any]):
    for method, path, operation in _crm_operations(schema):
        requirements = [scopes for requirement in operation["security"] for scopes in requirement.values()]
        required = [scope for scopes in requirements for scope in scopes]
        expected = ["crm:read"] if method == "GET" else ["crm:write"]
        assert required == expected, f"{method} {path}"


def test_every_crm_schema_property_has_a_description(schema: dict[str, Any]):
    names = _crm_schema_names(schema)

    assert "SubjectResponse" in names
    for name in sorted(names):
        for property_name, definition in schema["components"]["schemas"][name].get("properties", {}).items():
            assert definition.get("description", "").strip(), f"{name}.{property_name}"


def test_every_crm_path_and_query_parameter_has_a_description(schema: dict[str, Any]):
    documented = 0
    for method, path, operation in _crm_operations(schema):
        for parameter in operation.get("parameters", []):
            documented += 1
            assert parameter.get("description", "").strip(), f"{method} {path}: {parameter['name']}"

    assert documented


def test_every_crm_path_parameter_has_examples(schema: dict[str, Any]):
    for method, path, operation in _crm_operations(schema):
        for parameter in operation.get("parameters", []):
            if parameter["in"] == "path":
                assert parameter.get("examples") or parameter["schema"].get("examples"), (
                    f"{method} {path}: {parameter['name']}"
                )


def test_party_detail_is_a_discriminated_union_of_person_and_company(schema: dict[str, Any]):
    assert schema["components"]["schemas"]["PartyDetail"] == {
        "oneOf": [
            {"$ref": "#/components/schemas/PersonDetail"},
            {"$ref": "#/components/schemas/CompanyDetail"},
        ],
        "discriminator": {
            "propertyName": "type",
            "mapping": {
                "person": "#/components/schemas/PersonDetail",
                "company": "#/components/schemas/CompanyDetail",
            },
        },
    }


def test_party_detail_is_referenced_by_exactly_one_operation(schema: dict[str, Any]):
    referencing = [
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method in HTTP_METHODS and "PartyDetail" in _refs(operation)
    ]
    other_schemas = [
        name for name, definition in schema["components"]["schemas"].items() if "PartyDetail" in _refs(definition)
    ]

    assert referencing == ["crm_get_party"]
    assert other_schemas == []


def test_the_typed_write_routes_reference_their_union_member_directly(schema: dict[str, Any]):
    paths = schema["paths"]

    for path, method, status, member in [
        ("/api/v1/crm/persons", "post", "201", "PersonDetail"),
        ("/api/v1/crm/persons/{party_id}", "patch", "200", "PersonDetail"),
        ("/api/v1/crm/companies", "post", "201", "CompanyDetail"),
        ("/api/v1/crm/companies/{party_id}", "patch", "200", "CompanyDetail"),
    ]:
        response = paths[path][method]["responses"][status]
        assert response["content"]["application/json"]["schema"] == {"$ref": f"#/components/schemas/{member}"}


def test_the_union_members_differ_in_their_required_fields(schema: dict[str, Any]):
    """The generated client ignores the discriminator and tries the members in order."""
    person = set(schema["components"]["schemas"]["PersonDetail"]["required"])
    company = set(schema["components"]["schemas"]["CompanyDetail"]["required"])

    assert person - company
    assert company - person


def test_every_property_of_a_create_request_carries_examples(schema: dict[str, Any]):
    create_requests = {
        name: definition
        for name, definition in schema["components"]["schemas"].items()
        if name.endswith("CreateRequest") and name in _crm_schema_names(schema)
    }

    assert {"PersonCreateRequest", "CompanyCreateRequest", "ContactInfoCreateRequest"} <= set(create_requests)
    for name, definition in create_requests.items():
        for property_name, property_schema in definition["properties"].items():
            assert property_schema.get("examples"), f"{name}.{property_name}"
