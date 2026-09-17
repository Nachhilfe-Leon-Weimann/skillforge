from typing import Any

from app.core.errors import DomainError

from .errors import ApiError, status_for
from .schemas import ErrorResponse

OpenAPIResponses = dict[int | str, dict[str, Any]]


def error_responses(*errors: type[DomainError] | ApiError) -> OpenAPIResponses:
    """Document the errors an endpoint can produce: ``responses=error_responses(XNotFoundError)``.

    Takes domain error classes and ``ApiError`` declarations. A domain error's status comes from
    ``status_for`` - the same table the exception handlers use - so the docs cannot drift from
    runtime. Examples are keyed by ``code``, which makes a renamed error visible in the
    ``openapi.json`` diff.
    """
    documented_by_status: dict[int, dict[str, str]] = {}
    for error in errors:
        status, code, detail = _documented(error)
        documented_by_status.setdefault(status, {})[code] = detail

    return {
        status: {
            "model": ErrorResponse,
            "description": " / ".join(dict.fromkeys(documented.values())),
            "content": {
                "application/json": {
                    "examples": {
                        code: {"value": {"detail": detail, "code": code}} for code, detail in documented.items()
                    },
                },
            },
        }
        for status, documented in documented_by_status.items()
    }


def _documented(error: type[DomainError] | ApiError) -> tuple[int, str, str]:
    if isinstance(error, ApiError):
        return error.status_code, error.code, error.detail

    return status_for(error), error.code, error.message
