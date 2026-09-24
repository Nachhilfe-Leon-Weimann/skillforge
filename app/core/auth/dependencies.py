from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import SecurityScopes

from app.core.db.dependencies import DBSession
from app.core.logging import bind_request_log_context

from .config import AuthSettings
from .principal import ApplicationPrincipal, Principal, UserPrincipal
from .reach import Access, resolve_reach
from .scopes import BASE_OF, OWN_VARIANT, Scope, expand, format_scopes
from .security import oauth2_scheme
from .tokens import TokenValidationError, validate_access_token


def get_auth_settings() -> AuthSettings:
    from app.core.config import get_settings

    return get_settings().auth


AuthConfig = Annotated[AuthSettings, Depends(get_auth_settings)]
"""The auth settings of a request, as a parameter type."""


async def get_current_principal(
    request: Request,
    security_scopes: SecurityScopes,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    settings: AuthConfig,
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
        raise _missing_scope()

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

    A reach-qualified scope (a key of ``BASE_OF``) raises ``ValueError`` when the route is
    declared: a route guarded here serves every record, so it demands the unqualified scope, and a
    token restricted to its reach gets a ``403`` instead of a leak (ADR 0008).
    """
    return Security(get_current_principal, scopes=_unqualified(required_scopes))


def require_application_scopes(*required_scopes: Scope | str) -> Any:
    """Return the ``Security`` marker that admits an application principal holding the given scopes.

    Used as a parameter: ``Annotated[ApplicationPrincipal, require_application_scopes(Scope.X)]``. The
    scopes reach the nested ``get_current_principal`` - and with it the operation's OpenAPI ``security`` and
    its 403 - and ``require_application`` refuses a person's token whatever it carries. A reach-qualified
    scope raises ``ValueError`` as in ``require_scopes``.
    """
    return Security(require_application, scopes=_unqualified(required_scopes))


def _unqualified(scopes: Sequence[Scope | str]) -> list[str]:
    """The scopes of a guard as strings; a reach-qualified one is refused when the route is declared."""
    for scope in scopes:
        if scope in BASE_OF:
            raise ValueError(f"a scope guard cannot demand the reach-qualified scope {scope}")

    return [str(scope) for scope in scopes]


def require_access(scope: Scope) -> Any:
    """Return the ``Security`` marker of a reach-aware route: the parameter it guards receives an ``Access``.

    Use it as a parameter typed ``Annotated[Access, require_access(Scope.X)]``. The marker declares
    the reach-qualified ``OWN_VARIANT[scope]``, which the unqualified scope implies: a token carrying
    ``scope`` gets ``Access.all()`` without a query, a person restricted to ``:own`` gets their reach.
    ``customize_openapi`` documents the two as alternative requirements.

    A scope without an entry in ``OWN_VARIANT`` raises ``ValueError`` when the route is declared: no
    reach can restrict it, so ``require_scopes`` guards it.
    """
    if scope not in OWN_VARIANT:
        raise ValueError(f"require_access needs a scope with a reach-qualified variant, not {scope}")

    return Security(_access_for(scope), scopes=[OWN_VARIANT[scope]])


@cache
def _access_for(scope: Scope) -> Callable[..., Awaitable[Access]]:
    """Build the dependency behind ``require_access(scope)`` - one per scope, so FastAPI caches it per request."""

    async def access(
        request: Request,
        security_scopes: SecurityScopes,
        principal: Annotated[Principal, Depends(get_current_principal)],
        session: DBSession,
    ) -> Access:
        if scope in expand(principal.scopes):
            return Access.all()

        match principal:
            case UserPrincipal(party_id=party_id):
                return Access.of(await resolve_reach(session, party_id))
            case _:
                # An application has no party and so no reach: the 403 of a missing scope.
                bind_request_log_context(
                    request,
                    auth_reason="wrong_principal_type",
                    client_id=principal.client_id,
                    principal_type=principal.principal_type,
                    required_scopes=sorted(security_scopes.scopes),
                )
                raise _missing_scope()

    return access


def _missing_scope() -> HTTPException:
    """The 403 of a token that lacks what a route demands; ``FORBIDDEN_EXAMPLES`` documents its body."""
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")


def _authenticate_header(scopes: Sequence[str]) -> str:
    if scopes:
        return f'Bearer scope="{format_scopes(scopes)}"'

    return "Bearer"
