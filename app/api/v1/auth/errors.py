"""Errors the auth API declares itself. The codes are the OAuth2 ones (RFC 6749, section 5.2)."""

from app.api.v1.common import ApiError

INVALID_REQUEST = ApiError(422, code="invalid_request", detail="A required parameter is missing")
INVALID_CLIENT = ApiError(
    401,
    code="invalid_client",
    detail="Invalid client credentials",
    headers={"WWW-Authenticate": "Bearer"},
)
INVALID_GRANT = ApiError(400, code="invalid_grant", detail="Invalid credentials or refresh token")
INVALID_SCOPE = ApiError(400, code="invalid_scope", detail="Invalid requested scope")
UNAUTHORIZED_CLIENT = ApiError(400, code="unauthorized_client", detail="Client may not use this grant")
UNSUPPORTED_GRANT_TYPE = ApiError(400, code="unsupported_grant_type", detail="Unsupported grant_type")
