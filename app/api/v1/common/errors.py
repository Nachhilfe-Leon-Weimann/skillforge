"""Maps the HTTP-agnostic error taxonomy to the error envelope (ADR 0006).

``STATUS_BY_ERROR`` is the single place that knows which category becomes which status code. It
feeds the exception handlers below and the ``error_responses`` docs helper, so runtime behavior
and OpenAPI docs cannot drift.
"""

import re
from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import ConflictError, DomainError, DomainValidationError, NotFoundError

from .schemas import ErrorResponse, FieldError

STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    DomainValidationError: 422,
}

VALIDATION_ERROR_CODE = "validation_error"
VALIDATION_ERROR_DETAIL = "Request validation failed"

_NON_CODE_CHARACTERS = re.compile(r"[^a-z0-9]+")


def status_for(error_type: type[DomainError]) -> int:
    """Return the status code of the first mapped category along the error's MRO."""
    for base in error_type.__mro__:
        if issubclass(base, DomainError) and base in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[base]

    raise LookupError(
        f"{error_type.__name__} is not mapped to a status code: derive it from a category in "
        "app/core/errors.py (NotFoundError, ConflictError, DomainValidationError)."
    )


def public_detail(error: DomainError) -> str:
    """Return the text a client may see: the instance message only if the class exposes it."""
    instance_message = str(error)
    if error.expose_message and instance_message:
        return instance_message

    return error.message


def code_for_status(status_code: int) -> str:
    """Derive a coarse code from the status phrase (401 -> ``unauthorized``)."""
    return _NON_CODE_CHARACTERS.sub("_", _phrase_for(status_code).lower()).strip("_")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> Response:
        return _envelope(status_for(type(exc)), ErrorResponse(detail=public_detail(exc), code=exc.code))

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, exc: RequestValidationError) -> Response:
        errors = [
            FieldError(loc=list(error["loc"]), message=error["msg"], type=error["type"]) for error in exc.errors()
        ]
        return _envelope(422, ErrorResponse(detail=VALIDATION_ERROR_DETAIL, code=VALIDATION_ERROR_CODE, errors=errors))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        if not is_body_allowed_for_status_code(exc.status_code):
            return Response(status_code=exc.status_code, headers=exc.headers)

        detail = exc.detail if isinstance(exc.detail, str) else _phrase_for(exc.status_code)
        return _envelope(
            exc.status_code,
            ErrorResponse(detail=detail, code=code_for_status(exc.status_code)),
            headers=exc.headers,
        )


def _phrase_for(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP error"


def _envelope(status_code: int, body: ErrorResponse, *, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True), headers=headers)
