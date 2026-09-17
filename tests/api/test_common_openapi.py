from typing import Any

import pytest
from fastapi import APIRouter, FastAPI

from app.api.v1.common import ErrorResponse
from app.api.v1.common.openapi import customize_openapi, operation_id
from app.core.auth import Scope, require_scopes

ERROR_RESPONSE_REF = {"$ref": "#/components/schemas/ErrorResponse"}


def _operation_ids(app: FastAPI) -> set[str]:
    return {operation["operationId"] for item in app.openapi()["paths"].values() for operation in item.values()}


def test_operation_id_joins_the_first_tag_and_the_function_name():
    app = FastAPI(generate_unique_id_function=operation_id)

    @app.get("/jobs", tags=["bot"])
    async def list_jobs() -> None: ...

    assert _operation_ids(app) == {"bot_list_jobs"}


def test_operation_id_ignores_a_trailing_endpoint_suffix():
    app = FastAPI(generate_unique_id_function=operation_id)

    @app.get("/jobs", tags=["bot"])
    async def list_jobs_endpoint() -> None: ...

    assert _operation_ids(app) == {"bot_list_jobs"}


def test_operation_id_does_not_stutter_when_the_function_name_starts_with_the_tag():
    app = FastAPI(generate_unique_id_function=operation_id)

    @app.get("/health", tags=["system"])
    async def system_health_check() -> None: ...

    assert _operation_ids(app) == {"system_health_check"}


def test_operation_id_uses_the_tags_merged_from_nested_routers():
    leaf = APIRouter(prefix="/jobs")

    @leaf.get("")
    async def list_jobs_endpoint() -> None: ...

    domain = APIRouter(prefix="/bot", tags=["bot"])
    domain.include_router(leaf)
    version = APIRouter(prefix="/api/v1")
    version.include_router(domain)
    app = FastAPI(generate_unique_id_function=operation_id)
    app.include_router(version)

    assert _operation_ids(app) == {"bot_list_jobs"}


def test_untagged_route_is_rejected_at_registration():
    app = FastAPI(generate_unique_id_function=operation_id)

    with pytest.raises(RuntimeError, match="/untagged"):

        @app.get("/untagged")
        async def untagged() -> None: ...


def _customized_app() -> FastAPI:
    app = FastAPI()

    @app.get("/guarded", dependencies=[require_scopes(Scope.BOT_READ)])
    async def guarded() -> None: ...

    @app.get("/guarded-twice", dependencies=[require_scopes(Scope.BOT_READ, Scope.BOT_WRITE)])
    async def guarded_twice() -> None: ...

    @app.get("/open")
    async def unguarded() -> None: ...

    @app.get("/items/{item_id}")
    async def read_item(item_id: int) -> None: ...

    @app.get("/rules/{rule_id}", responses={422: {"model": ErrorResponse, "description": "Rule rejected"}})
    async def read_rule(rule_id: int) -> None: ...

    customize_openapi(app)
    return app


def _responses(app: FastAPI, path: str) -> dict[str, Any]:
    return app.openapi()["paths"][path]["get"]["responses"]


def _dangling_refs(schema: dict[str, Any]) -> set[str]:
    return {
        ref for ref in _refs(schema) if ref.removeprefix("#/components/schemas/") not in schema["components"]["schemas"]
    }


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        own = {node["$ref"]} if isinstance(node.get("$ref"), str) else set()
        return own.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


def test_guarded_operation_documents_401_and_403_with_the_error_envelope():
    responses = _responses(_customized_app(), "/guarded")

    assert responses["401"]["description"] == "Missing or invalid bearer token"
    assert responses["401"]["content"]["application/json"]["schema"] == ERROR_RESPONSE_REF
    assert responses["403"]["content"]["application/json"]["schema"] == ERROR_RESPONSE_REF


def test_forbidden_description_names_the_required_scope():
    responses = _responses(_customized_app(), "/guarded")

    assert responses["403"]["description"] == "Missing required scope: bot:read"


def test_forbidden_description_names_all_required_scopes():
    responses = _responses(_customized_app(), "/guarded-twice")

    assert responses["403"]["description"] == "Missing required scopes: bot:read, bot:write"


def test_auth_error_examples_show_the_envelope_with_detail_and_code():
    responses = _responses(_customized_app(), "/guarded")

    unauthorized = responses["401"]["content"]["application/json"]["examples"]
    forbidden = responses["403"]["content"]["application/json"]["example"]
    assert unauthorized["missing_token"]["value"] == {"detail": "Not authenticated", "code": "unauthorized"}
    assert unauthorized["invalid_token"]["value"] == {
        "detail": "Invalid authentication credentials",
        "code": "unauthorized",
    }
    assert forbidden == {"detail": "Not enough permissions", "code": "forbidden"}


def test_unguarded_operation_documents_no_auth_errors():
    responses = _responses(_customized_app(), "/open")

    assert "401" not in responses
    assert "403" not in responses


def _app_without_envelope_references() -> FastAPI:
    """Only a guarded, parameterless route: nothing makes FastAPI register ``ErrorResponse`` itself."""
    app = FastAPI()

    @app.get("/guarded", dependencies=[require_scopes(Scope.BOT_READ)])
    async def guarded() -> None: ...

    return app


def test_fastapi_alone_does_not_register_the_error_envelope():
    schema = _app_without_envelope_references().openapi()

    assert "ErrorResponse" not in schema.get("components", {}).get("schemas", {})


def test_error_envelope_schema_is_registered_even_if_no_route_references_it():
    app = _app_without_envelope_references()
    customize_openapi(app)

    schemas = app.openapi()["components"]["schemas"]
    assert schemas["ErrorResponse"]["required"] == ["detail", "code"]
    assert schemas["ErrorResponse"]["properties"]["detail"]["type"] == "string"


def test_error_envelope_schema_is_registered_together_with_its_nested_schemas():
    app = _app_without_envelope_references()
    customize_openapi(app)

    schema = app.openapi()
    assert "$defs" not in schema["components"]["schemas"]["ErrorResponse"]
    assert "FieldError" in schema["components"]["schemas"]
    assert _dangling_refs(schema) == set()


def test_auto_generated_422_is_replaced_by_the_error_envelope():
    responses = _responses(_customized_app(), "/items/{item_id}")

    assert responses["422"]["description"] == "Request validation failed"
    assert responses["422"]["content"]["application/json"]["schema"] == ERROR_RESPONSE_REF
    example = responses["422"]["content"]["application/json"]["example"]
    assert example["code"] == "validation_error"
    assert example["errors"]


def test_framework_validation_schemas_leave_the_contract():
    schema = _customized_app().openapi()

    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]
    assert _dangling_refs(schema) == set()


def test_422_declared_by_a_route_is_left_untouched():
    responses = _responses(_customized_app(), "/rules/{rule_id}")

    assert responses["422"]["description"] == "Rule rejected"
    assert "example" not in responses["422"]["content"]["application/json"]


def test_customized_schema_is_built_once_and_cached():
    app = _customized_app()

    assert app.openapi() is app.openapi()
    assert app.openapi_schema is app.openapi()
