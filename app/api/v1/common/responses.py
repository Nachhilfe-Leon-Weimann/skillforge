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
