import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from skillcore.logging import get_logger
from structlog import contextvars

request_logger = get_logger("app.request")
_REQUEST_LOG_CONTEXT_STATE = "request_log_context"


def bind_request_log_context(request: Request | None = None, **values: object) -> None:
    context = {key: value for key, value in values.items() if value is not None}
    contextvars.bind_contextvars(**context)

    if request is not None:
        request_context = getattr(request.state, _REQUEST_LOG_CONTEXT_STATE, {})
        setattr(request.state, _REQUEST_LOG_CONTEXT_STATE, request_context | context)


def register_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started_at = time.perf_counter()
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        contextvars.bind_contextvars(request_id=request_id)

        try:
            try:
                response = await call_next(request)
            except Exception:
                request_logger.exception(
                    "http_request_failed",
                    method=request.method,
                    path=request.url.path,
                    status_code=500,
                    duration_ms=_duration_ms(started_at),
                    client_ip=_client_ip(request),
                )
                raise

            response.headers["x-request-id"] = request_id
            _log_http_request(request, response, started_at)
            return response
        finally:
            contextvars.clear_contextvars()


def _log_http_request(request: Request, response: Response, started_at: float) -> None:
    status_code = response.status_code
    log = request_logger.info
    event = "http_request_completed"

    if status_code >= 500:
        log = request_logger.error
        event = "http_request_failed"
    elif status_code == 404:
        log = request_logger.warning
        event = "http_request_not_found"
    elif status_code == 403:
        log = request_logger.warning
        event = "http_request_forbidden"
    elif status_code == 401:
        log = request_logger.warning
        event = "http_request_unauthorized"
    elif status_code >= 400:
        log = request_logger.warning
        event = "http_request_rejected"

    log(
        event,
        method=request.method,
        path=request.url.path,
        route=_route_path(request),
        endpoint=_endpoint_name(request),
        status_code=status_code,
        duration_ms=_duration_ms(started_at),
        client_ip=_client_ip(request),
        **_request_log_context(request),
    )


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None

    return request.client.host


def _route_path(request: Request) -> str | None:
    route = request.scope.get("route")
    if route is None:
        return None

    return getattr(route, "path", None)


def _endpoint_name(request: Request) -> str | None:
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return None

    return getattr(endpoint, "__name__", None)


def _request_log_context(request: Request) -> dict[str, object]:
    return getattr(request.state, _REQUEST_LOG_CONTEXT_STATE, {})
