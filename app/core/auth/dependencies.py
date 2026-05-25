from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import SecurityScopes

from app.core.logging import bind_request_log_context

from .config import AuthSettings
from .principal import Principal
from .scopes import Scope
from .security import oauth2_scheme
from .tokens import PRINCIPAL_TYPE_APPLICATION, TokenValidationError, validate_access_token


def get_auth_settings() -> AuthSettings:
    from app.core.config import get_settings

    return get_settings().auth


async def get_current_principal(
    request: Request,
    security_scopes: SecurityScopes,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> Principal:
    authenticate_value = _authenticate_header(security_scopes.scopes)
    if token is None:
        bind_request_log_context(
            request,
            auth_reason="missing_token",
            required_scopes=sorted(security_scopes.scopes),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": authenticate_value},
        )

    try:
        principal = validate_access_token(token, settings)
    except TokenValidationError as exc:
        bind_request_log_context(
            request,
            auth_reason="invalid_token",
            required_scopes=sorted(security_scopes.scopes),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": authenticate_value},
        ) from exc

    missing_scopes = set(security_scopes.scopes) - principal.scopes
    if missing_scopes:
        bind_request_log_context(
            request,
            auth_reason="missing_scopes",
            client_id=principal.client_id,
            principal_type=principal.principal_type,
            required_scopes=sorted(security_scopes.scopes),
            missing_scopes=sorted(missing_scopes),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )

    return principal


async def require_application(
    request: Request,
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    if principal.principal_type != PRINCIPAL_TYPE_APPLICATION:
        bind_request_log_context(
            request,
            auth_reason="wrong_principal_type",
            principal_type=principal.principal_type,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Application principal required",
        )

    return principal


def require_scopes(*required_scopes: Scope | str):
    scope_values = [str(scope) for scope in required_scopes]

    async def dependency(
        principal: Annotated[Principal, Security(get_current_principal, scopes=scope_values)],
    ) -> Principal:
        return principal

    return dependency


def _authenticate_header(scopes: Sequence[str]) -> str:
    if scopes:
        return f'Bearer scope="{" ".join(scopes)}"'

    return "Bearer"
