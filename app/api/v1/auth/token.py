import base64
import binascii
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.auth import (
    CreatedAccessToken,
    InvalidClientCredentialsError,
    InvalidClientScopeError,
    issue_client_token,
)
from app.core.auth.dependencies import AuthConfig
from app.core.db.dependencies import get_db_session
from app.core.logging import bind_request_log_context

from .errors import INVALID_CLIENT, INVALID_REQUEST, INVALID_SCOPE, UNSUPPORTED_GRANT_TYPE
from .schemas import AccessTokenResponse

router = APIRouter()

IssueClientToken = Callable[..., Awaitable[CreatedAccessToken]]


@dataclass(frozen=True)
class ClientTokenForm:
    grant_type: str
    client_id: str
    client_secret: str
    scope: str | None


def get_issue_client_token() -> IssueClientToken:
    return issue_client_token


async def get_client_token_form(
    request: Request,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
    scope: Annotated[str | None, Form()] = None,
) -> ClientTokenForm:
    resolved_client_id = client_id
    resolved_client_secret = client_secret
    basic_auth = _get_basic_credentials(request)
    if basic_auth is not None:
        resolved_client_id, resolved_client_secret = basic_auth

    if not resolved_client_id or not resolved_client_secret:
        bind_request_log_context(request, auth_reason="missing_client_credentials")
        raise INVALID_REQUEST.exception()

    return ClientTokenForm(
        grant_type=grant_type,
        client_id=resolved_client_id,
        client_secret=resolved_client_secret,
        scope=scope,
    )


def _get_basic_credentials(request: Request) -> tuple[str, str] | None:
    authorization = request.headers.get("Authorization")
    scheme, encoded = get_authorization_scheme_param(authorization)
    if scheme.lower() != "basic" or not encoded:
        return None

    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except binascii.Error, UnicodeDecodeError:
        return None

    if ":" not in decoded:
        return None

    username, password = decoded.split(":", 1)
    return username, password


@router.post(
    "/token",
    response_model=AccessTokenResponse,
    responses=error_responses(UNSUPPORTED_GRANT_TYPE, INVALID_SCOPE, INVALID_CLIENT, INVALID_REQUEST),
)
async def create_token(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: AuthConfig,
    issue_token: Annotated[IssueClientToken, Depends(get_issue_client_token)],
    form: Annotated[ClientTokenForm, Depends(get_client_token_form)],
) -> AccessTokenResponse | JSONResponse:
    if form.grant_type != "client_credentials":
        bind_request_log_context(request, auth_reason="unsupported_grant_type")
        raise UNSUPPORTED_GRANT_TYPE.exception()

    try:
        token: CreatedAccessToken = await issue_token(
            session,
            settings,
            client_id=form.client_id,
            client_secret=form.client_secret,
            requested_scopes=form.scope,
        )
    # Both denials are *returned*, not raised: issue_token has written a TOKEN_DENIED audit entry,
    # and raising would roll the session back and lose it.
    except InvalidClientCredentialsError:
        bind_request_log_context(request, auth_reason="invalid_client_credentials", client_id=form.client_id)
        return INVALID_CLIENT.response()
    except InvalidClientScopeError:
        bind_request_log_context(request, auth_reason="invalid_requested_scope", client_id=form.client_id)
        return INVALID_SCOPE.response()

    return AccessTokenResponse.from_created_token(token)
