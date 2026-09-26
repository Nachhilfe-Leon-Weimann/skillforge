import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from skillcore.logging import get_logger
from structlog import contextvars

request_logger = get_logger("app.request")
REQUEST_ID_HEADER = "x-request-id"
_REQUEST_ID_STATE = "request_id"
_REQUEST_LOG_CONTEXT_STATE = "request_log_context"

# Path prefixes treated as health/readiness probes: silenced while healthy and
# warned (not errored) on 5xx, since they are polled constantly and report
# health via the status code.
_PROBE_PATH_PREFIXES = ("/health",)

# Path segments redacted from the request log, keyed by the segment right before them: the segment
# after each key names a Discord user, which appears in audit rows only, never in the request log
# (bot-decoupling spec, "Security rules"). Segment-based, not routing-based: it survives a
# trailing-slash redirect or an unmatched sub-path, where FastAPI never populates `path_params`.
REDACTED_PATH_SEGMENTS = {"discord-links": "{discord_user_id}"}


def bind_request_log_context(request: Request | None = None, **values: object) -> None:
    context = {key: value for key, value in values.items() if value is not None}
    contextvars.bind_contextvars(**context)

    if request is not None:
        request_context = getattr(request.state, _REQUEST_LOG_CONTEXT_STATE, {})
        setattr(request.state, _REQUEST_LOG_CONTEXT_STATE, request_context | context)


def get_request_id(request: Request) -> str | None:
    """Return the id the request-logging middleware assigned to this request, if it ran.

    Lives in ``request.state``, which hangs off the ASGI scope: it stays readable where the
    middleware's response hook is not, e.g. in the catch-all 500 handler that runs outside of it.
    """
    return getattr(request.state, _REQUEST_ID_STATE, None)


def register_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started_at = time.perf_counter()
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        setattr(request.state, _REQUEST_ID_STATE, request_id)
        contextvars.bind_contextvars(request_id=request_id)

        try:
            try:
                response = await call_next(request)
            except Exception:
                request_logger.exception(
                    "http_request_failed",
                    method=request.method,
                    path=_logged_path(request.url.path),
                    status_code=500,
                    duration_ms=_duration_ms(started_at),
                    client_ip=_client_ip(request),
                )
                raise

            response.headers[REQUEST_ID_HEADER] = request_id
            _log_http_request(request, response, started_at)
            return response
        finally:
            contextvars.clear_contextvars()


def _log_http_request(request: Request, response: Response, started_at: float) -> None:
    status_code = response.status_code
    is_probe = _is_probe_path(request.url.path)

    # Probe endpoints (health/readiness) are polled constantly and encode health
    # in the status code. Stay silent while healthy; a 5xx means the probe
    # reported a problem, not that the request itself failed.
    if is_probe and status_code < 500:
        return

    log = request_logger.info
    event = "http_request_completed"

    if is_probe:  # implies status_code >= 500 here
        log = request_logger.warning
        event = "http_probe_unhealthy"
    elif status_code >= 500:
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
        path=_logged_path(request.url.path),
        route=_route_path(request),
        endpoint=_endpoint_name(request),
        status_code=status_code,
        duration_ms=_duration_ms(started_at),
        client_ip=_client_ip(request),
        **_request_log_context(request),
    )


def _is_probe_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in _PROBE_PATH_PREFIXES)


def _duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 2)


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None

    return request.client.host


def _logged_path(path: str) -> str:
    """The path to log: the segment after a redacted key replaced by its placeholder, else `path` as it is.

    Works on ``/``-separated segments of the concrete URL, not on routing state (``path_params``, the
    matched route): a trailing-slash redirect and an unmatched sub-path never populate either, and a
    substring replacement would corrupt an unrelated segment that happens to contain the same digits
    (``/v1/...``) - a pure function is also the easiest of the two to unit-test. Compares each non-empty
    segment against the previous non-empty one, not the one right before it: a doubled or tripled slash
    (ASGI decodes ``%2F`` the same way) still redacts the ID. An empty segment is itself left alone, so
    the path's shape - how many slashes, where - is preserved.
    """
    segments = path.split("/")
    previous = ""
    for index, segment in enumerate(segments):
        if not segment:
            continue
        if placeholder := REDACTED_PATH_SEGMENTS.get(previous):
            segments[index] = placeholder
        previous = segment

    return "/".join(segments)


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
