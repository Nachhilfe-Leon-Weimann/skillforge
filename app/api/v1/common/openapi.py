from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .schemas import ErrorResponse

ENDPOINT_SUFFIX = "_endpoint"
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})
SCHEMA_REF_TEMPLATE = "#/components/schemas/{model}"

OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "auth",
        "description": "OAuth2 client-credentials token issuance and application client management.",
    },
    {
        "name": "bot",
        "description": (
            "SkillBot state API: runtime contexts, jobs, two-phase operations, command environments, "
            "users and authorization."
        ),
    },
    {
        "name": "crm",
        "description": "Customer relationship management: parties and the data attached to them.",
    },
    {
        "name": "system",
        "description": "Service root plus liveness and health probes for dependencies and workers.",
    },
]

# Bodies raised by ``get_current_principal`` in ``app/core/auth/dependencies.py``.
UNAUTHORIZED_EXAMPLES: dict[str, dict[str, Any]] = {
    "missing_token": {"value": {"detail": "Not authenticated"}},
    "invalid_token": {"value": {"detail": "Invalid authentication credentials"}},
}
FORBIDDEN_EXAMPLE: dict[str, Any] = {"detail": "Not enough permissions"}


def operation_id(route: APIRoute) -> str:
    """Build the operation ID ``{tag}_{function_name}`` for a route.

    The first tag is the domain (and the generated client's module). A trailing ``_endpoint`` and
    a leading ``{tag}_`` are stripped from the function name, so ``list_jobs_endpoint`` and
    ``list_jobs`` both become ``bot_list_jobs`` and ``system_health_check`` does not stutter.
    """
    if not route.tags:
        raise RuntimeError(
            f"Route {route.path!r} ({route.name}) has no tag. Set exactly one domain tag on its router: "
            "the tag prefixes the operation ID and names the generated client's module."
        )

    tag = str(route.tags[0])
    name = route.name.removesuffix(ENDPOINT_SUFFIX).removeprefix(f"{tag}_")
    return f"{tag}_{name}"


def customize_openapi(app: FastAPI) -> None:
    """Wrap ``app.openapi`` with the post-processing that derives docs from declared facts.

    Call once, after all routers are included. The result is cached in ``app.openapi_schema``
    like FastAPI's own schema, so the post-processing runs a single time.
    """
    generate_openapi = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = generate_openapi()
            _document_auth_errors(schema)
            app.openapi_schema = schema
        return app.openapi_schema

    # Overriding the bound method is FastAPI's documented way to extend the schema.
    app.openapi = openapi  # ty: ignore[invalid-assignment]


def _document_auth_errors(schema: dict[str, Any]) -> None:
    """Document 401/403 on every operation that declares a ``security`` requirement."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.setdefault(ErrorResponse.__name__, ErrorResponse.model_json_schema(ref_template=SCHEMA_REF_TEMPLATE))

    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method not in HTTP_METHODS or not operation.get("security"):
                continue

            responses = operation.setdefault("responses", {})
            responses["401"] = _error_response("Missing or invalid bearer token", examples=UNAUTHORIZED_EXAMPLES)
            responses["403"] = _error_response(_forbidden_description(operation), example=FORBIDDEN_EXAMPLE)


def _forbidden_description(operation: dict[str, Any]) -> str:
    scopes = list(dict.fromkeys(scope for requirement in operation["security"] for scope in _scopes_of(requirement)))
    match scopes:
        case []:
            return "Not enough permissions"
        case [scope]:
            return f"Missing required scope: {scope}"
        case _:
            return f"Missing required scopes: {', '.join(scopes)}"


def _scopes_of(requirement: dict[str, list[str]]) -> list[str]:
    return [scope for scopes in requirement.values() for scope in scopes]


def _error_response(description: str, **content: Any) -> dict[str, Any]:
    schema_ref = {"$ref": SCHEMA_REF_TEMPLATE.format(model=ErrorResponse.__name__)}
    return {
        "description": description,
        "content": {"application/json": {"schema": schema_ref, **content}},
    }
