from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import SecurityScopes

from .config import AuthSettings
from .principal import Principal
from .scopes import Scope
from .security import oauth2_scheme
from .tokens import PRINCIPAL_TYPE_APPLICATION, TokenValidationError, validate_access_token


def get_auth_settings() -> AuthSettings:
    from app.core.config import get_settings

    return get_settings().auth


async def get_current_principal(
    security_scopes: SecurityScopes,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> Principal:
    authenticate_value = _authenticate_header(security_scopes.scopes)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": authenticate_value},
        )

    try:
        principal = validate_access_token(token, settings)
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": authenticate_value},
        ) from exc

    missing_scopes = set(security_scopes.scopes) - principal.scopes
    if missing_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions",
        )

    return principal


async def require_application(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> Principal:
    if principal.principal_type != PRINCIPAL_TYPE_APPLICATION:
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
