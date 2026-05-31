from typing import Any

from .schemas import ErrorResponse

OpenAPIResponses = dict[int | str, dict[str, Any]]
OpenAPIResponse = dict[str, Any]


def error_response(description: str, *, detail: str | None = None) -> OpenAPIResponse:
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {
            "application/json": {
                "example": {"detail": detail or description},
            },
        },
    }


def auth_error_responses(*required_scopes: object) -> OpenAPIResponses:
    scope_values = [str(scope) for scope in required_scopes]
    missing_scope_description = "Missing required scope"
    if scope_values:
        missing_scope_description = f"Missing required scope: **{' '.join(scope_values)}**"

    return {
        401: _authentication_error_response(),
        403: error_response(missing_scope_description, detail="Not enough permissions"),
    }


def _authentication_error_response() -> OpenAPIResponse:
    return {
        "model": ErrorResponse,
        "description": "Missing or invalid bearer token",
        "content": {
            "application/json": {
                "examples": {
                    "missing_token": {"value": {"detail": "Not authenticated"}},
                    "invalid_token": {"value": {"detail": "Invalid authentication credentials"}},
                },
            },
        },
    }
