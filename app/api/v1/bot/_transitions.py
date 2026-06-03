"""Shared helpers for the transition (prepare/commit) endpoints."""

from fastapi import HTTPException, status

from app.api.v1.common import error_response
from app.api.v1.common.responses import OpenAPIResponses
from app.services.bot import (
    OperationNotFoundError,
    OperationNotPendingError,
    TransitionConflictError,
    TransitionValidationError,
)

OPERATION_NOT_FOUND = "Operation not found"
OPERATION_NOT_PENDING = "Operation is not in a prepared state (already committed, failed, or expired)"

PREPARE_RESPONSES: OpenAPIResponses = {
    409: error_response("Transition conflict"),
    422: error_response("Transition validation failed"),
}
COMMIT_RESPONSES: OpenAPIResponses = {
    404: error_response(OPERATION_NOT_FOUND),
    409: error_response("Transition conflict"),
}


def transition_http_exception(exc: Exception) -> HTTPException:
    """Map a transition domain error to its HTTP response.

    The service-layer messages are safe to surface (e.g. "Tutor student capacity reached").
    """
    if isinstance(exc, OperationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=OPERATION_NOT_FOUND)
    if isinstance(exc, OperationNotPendingError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc) or OPERATION_NOT_PENDING)
    if isinstance(exc, TransitionConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc) or "Transition conflict")
    if isinstance(exc, TransitionValidationError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc) or "Transition validation failed"
        )
    raise exc
