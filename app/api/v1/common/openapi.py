from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from .errors import VALIDATION_ERROR_CODE, VALIDATION_ERROR_DETAIL, code_for_status
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
    "missing_token": {"value": {"detail": "Not authenticated", "code": code_for_status(401)}},
    "invalid_token": {"value": {"detail": "Invalid authentication credentials", "code": code_for_status(401)}},
}
FORBIDDEN_EXAMPLE: dict[str, Any] = {"detail": "Not enough permissions", "code": code_for_status(403)}
VALIDATION_ERROR_EXAMPLE: dict[str, Any] = {
    "detail": VALIDATION_ERROR_DETAIL,
    "code": VALIDATION_ERROR_CODE,
    "errors": [
        {
            "loc": ["query", "limit"],
            "message": "Input should be less than or equal to 100",
            "type": "less_than_equal",
        }
    ],
}

# The schemas FastAPI generates for its built-in 422; replaced by the envelope (ADR 0006).
FRAMEWORK_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


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

    Call once, after all routers are included. Caching stays with FastAPI (``app.openapi_schema``,
    regenerated when routes change); a schema is post-processed exactly once, when FastAPI hands
    out a new one.

    The schema is built right away: FastAPI resolves included routers lazily, so without this a
    route rejected by ``operation_id`` would not fail at import but turn requests into 500s.
    """
    generate_openapi = app.openapi
    customized: dict[str, Any] | None = None

    def openapi() -> dict[str, Any]:
        nonlocal customized
        schema = generate_openapi()
        if schema is not customized:
            _register_error_envelope(schema)
            _document_auth_errors(schema)
            _unify_validation_errors(schema)
            customized = schema
        return schema

    # Overriding the bound method is FastAPI's documented way to extend the schema.
    app.openapi = openapi  # ty: ignore[invalid-assignment]
    app.openapi()


def _register_error_envelope(schema: dict[str, Any]) -> None:
    """Make sure ``ErrorResponse`` (and what it nests) exists even if no route references it."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    envelope = ErrorResponse.model_json_schema(ref_template=SCHEMA_REF_TEMPLATE)
    for name, nested in envelope.pop("$defs", {}).items():
        schemas.setdefault(name, nested)
    schemas.setdefault(ErrorResponse.__name__, envelope)


def _document_auth_errors(schema: dict[str, Any]) -> None:
    """Document 401/403 on every operation that declares a ``security`` requirement."""
    for operation in _operations(schema):
        if not operation.get("security"):
            continue

        responses = operation.setdefault("responses", {})
        responses["401"] = _error_response("Missing or invalid bearer token", examples=UNAUTHORIZED_EXAMPLES)
        responses["403"] = _error_response(_forbidden_description(operation), example=FORBIDDEN_EXAMPLE)


def _unify_validation_errors(schema: dict[str, Any]) -> None:
    """Replace FastAPI's auto-generated 422 with the envelope.

    A 422 a route declares itself stays as it is. FastAPI then omits its own 422, although a route
    that takes input can still fail request validation - so if the declared 422 lists ``examples``
    (as ``error_responses`` does), the validation example joins them.
    """
    framework_ref = {"$ref": SCHEMA_REF_TEMPLATE.format(model=FRAMEWORK_VALIDATION_SCHEMAS[0])}
    for operation in _operations(schema):
        content = operation.get("responses", {}).get("422", {}).get("content", {}).get("application/json", {})
        if content.get("schema") == framework_ref:
            operation["responses"]["422"] = _error_response(VALIDATION_ERROR_DETAIL, example=VALIDATION_ERROR_EXAMPLE)
        elif "examples" in content and (operation.get("parameters") or operation.get("requestBody")):
            content["examples"].setdefault(VALIDATION_ERROR_CODE, {"value": VALIDATION_ERROR_EXAMPLE})

    schemas = schema["components"]["schemas"]
    for name in FRAMEWORK_VALIDATION_SCHEMAS:
        if SCHEMA_REF_TEMPLATE.format(model=name) not in _refs(schema):
            schemas.pop(name, None)


def _operations(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for path_item in schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method in HTTP_METHODS:
                yield operation


def _refs(node: Any) -> set[str]:
    if isinstance(node, dict):
        own = {node["$ref"]} if isinstance(node.get("$ref"), str) else set()
        return own.union(*(_refs(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(_refs(value) for value in node))
    return set()


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
