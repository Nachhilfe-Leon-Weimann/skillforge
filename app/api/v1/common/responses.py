from typing import Any

from app.core.errors import DomainError

from .errors import status_for
from .schemas import ErrorResponse

OpenAPIResponses = dict[int | str, dict[str, Any]]
OpenAPIResponse = dict[str, Any]


def error_responses(*error_types: type[DomainError]) -> OpenAPIResponses:
    """Document the domain errors an endpoint can produce: ``responses=error_responses(XNotFoundError)``.

    The status code comes from ``status_for`` - the same table the exception handlers use - so the
    docs cannot drift from runtime. Examples are keyed by ``code``, which makes a renamed error
    visible in the ``openapi.json`` diff.
    """
    errors_by_status: dict[int, list[type[DomainError]]] = {}
    for error_type in error_types:
        errors_by_status.setdefault(status_for(error_type), []).append(error_type)

    return {
        status: {
            "model": ErrorResponse,
            "description": " / ".join(dict.fromkeys(error.message for error in errors)),
            "content": {
                "application/json": {
                    "examples": {
                        error.code: {"value": {"detail": error.message, "code": error.code}} for error in errors
                    },
                },
            },
        }
        for status, errors in errors_by_status.items()
    }


def error_response(description: str, *, detail: str | None = None) -> OpenAPIResponse:
    """Legacy per-status helper of the bot domain; goes away with its migration to the taxonomy."""
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {
            "application/json": {
                "example": {"detail": detail or description},
            },
        },
    }
