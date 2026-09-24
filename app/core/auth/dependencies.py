from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import SecurityScopes

from app.core.logging import bind_request_log_context

from .config import AuthSettings
from .principal import ApplicationPrincipal, Principal, UserPrincipal
from .scopes import OWN_VARIANT, Scope, expand, format_scopes
from .security import oauth2_scheme
from .tokens import TokenValidationError, validate_access_token


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

    if isinstance(principal, UserPrincipal):
        # Who the request speaks for, on every line it logs - never the session id.
        bind_request_log_context(
            request,
            principal_type=principal.principal_type,
            user_id=str(principal.principal_id),
            party_id=str(principal.party_id),
        )

    missing_scopes = set(security_scopes.scopes) - expand(principal.scopes)
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
) -> ApplicationPrincipal:
    if not isinstance(principal, ApplicationPrincipal):
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


def require_scopes(*required_scopes: Scope | str) -> Any:
    """Return the ``Security`` marker that guards a route with the given scopes.

    Guard only: ``dependencies=[require_scopes(Scope.X)]`` on the route decorator.
    Principal needed: a parameter typed ``Annotated[Principal, require_scopes(Scope.X)]``.
    In both positions the scopes land in the operation's OpenAPI ``security`` requirement.

    A reach-qualified scope (a value of ``OWN_VARIANT``) raises ``ValueError`` when the route is
    declared: a route guarded here serves every record, so it demands the unqualified scope, and a
    token restricted to its reach gets a ``403`` instead of a leak (ADR 0008).
    """
    for scope in required_scopes:
        if scope in OWN_VARIANT.values():
            raise ValueError(f"require_scopes cannot demand the reach-qualified scope {scope}")

    return Security(get_current_principal, scopes=[str(scope) for scope in required_scopes])


def _authenticate_header(scopes: Sequence[str]) -> str:
    if scopes:
        return f'Bearer scope="{format_scopes(scopes)}"'

    return "Bearer"
