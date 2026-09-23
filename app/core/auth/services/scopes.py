from collections.abc import Iterable, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClient, ApplicationClientScopeGrant, PermissionScope

from ..audit import AuditEventType, write_auth_audit_log
from ..scopes import Scope, canonical, expand, parse_scopes
from .clients import get_application_client
from .errors import ApplicationClientScopeGrantNotFoundError, InvalidClientScopeError


async def seed_default_scopes(session: AsyncSession) -> list[PermissionScope]:
    scopes: list[PermissionScope] = []
    for scope in Scope:
        permission_scope = await session.get(PermissionScope, scope.value)
        if permission_scope is None:
            permission_scope = PermissionScope(
                key=scope.value,
                description=scope.description,
                active=True,
            )
            session.add(permission_scope)
        else:
            permission_scope.description = scope.description
            permission_scope.active = True

        scopes.append(permission_scope)

    await session.flush()
    return scopes


async def grant_application_client_scopes(
    session: AsyncSession,
    *,
    client_id: str,
    scopes: Iterable[Scope | str],
) -> ApplicationClient:
    await seed_default_scopes(session)
    client = await get_application_client(session, client_id=client_id)
    scope_keys = parse_scopes(scopes)
    await grant_client_scopes(session, client=client, scope_keys=scope_keys)
    return await get_application_client(session, client_id=client.client_id)


async def revoke_application_client_scope(
    session: AsyncSession,
    *,
    client_id: str,
    scope_key: str,
) -> None:
    client = await get_application_client(session, client_id=client_id)
    grant = await session.get(ApplicationClientScopeGrant, (client.id, scope_key))
    if grant is None:
        raise ApplicationClientScopeGrantNotFoundError("Application client scope grant not found")

    await session.delete(grant)
    await session.flush()
    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type=AuditEventType.SCOPE_GRANT_REMOVED,
        success=True,
        detail=f"Removed scope {scope_key} from application client {client.client_id}.",
    )


def granted_active_scope_keys(scope_grants: list[ApplicationClientScopeGrant]) -> frozenset[str]:
    return frozenset(
        grant.scope_key
        for grant in scope_grants
        if grant.permission_scope is not None and grant.permission_scope.active
    )


async def grant_client_scopes(
    session: AsyncSession,
    *,
    client: ApplicationClient,
    scope_keys: frozenset[str],
) -> frozenset[str]:
    existing_scope_keys = set(
        (
            await session.execute(
                select(ApplicationClientScopeGrant.scope_key).where(
                    ApplicationClientScopeGrant.application_client_id == client.id
                )
            )
        )
        .scalars()
        .all()
    )
    granted_scope_keys: set[str] = set()

    for scope_key in sorted(scope_keys):
        permission_scope = await session.get(PermissionScope, scope_key)
        if permission_scope is None or not permission_scope.active:
            raise InvalidClientScopeError("Requested scopes are not known or active")

        granted_scope_keys.add(scope_key)
        if scope_key in existing_scope_keys:
            continue

        session.add(ApplicationClientScopeGrant(application_client=client, permission_scope=permission_scope))
        await write_auth_audit_log(
            session,
            principal_type="application",
            principal_id=client.id,
            event_type=AuditEventType.SCOPE_GRANT_ADDED,
            success=True,
            detail=f"Granted scope {scope_key} to application client {client.client_id}.",
        )

    await session.flush()
    return frozenset(granted_scope_keys)


def resolve_token_scopes(
    *,
    requested_scopes: Set[str],
    granted_scopes: Set[str],
    user_scopes: Set[str] | None = None,
) -> frozenset[str]:
    """Compute the canonical scopes of an issued token (ADR 0008, decision G).

    ``granted_scopes`` are the client's scope grants; they are always the ceiling - client
    credentials use them directly. For the ``password`` and ``refresh_token`` grants
    ``user_scopes`` narrows that ceiling further to what the user's roles allow: the client is the
    ceiling for every scope a user token can carry, ``account:self`` included, with no exemption.

    With no scope requested, the token gets the canonical form of the ceiling; a request is
    accepted only if every requested scope lies within it, and the token then carries the
    canonical form of exactly what was requested (requesting fewer scopes is always allowed). A
    ceiling, or a requested set, that comes out empty is ``invalid_scope``.
    """
    ceiling = expand(granted_scopes)
    available = ceiling if user_scopes is None else ceiling & expand(user_scopes)
    if requested_scopes - available:
        raise InvalidClientScopeError("Requested scopes are not granted")

    token_scopes = canonical(requested_scopes or available)
    if not token_scopes:
        if user_scopes is not None and ceiling:
            raise InvalidClientScopeError("Client grants and user scopes have no scope in common")

        raise InvalidClientScopeError("Client has no active scope grants")

    return token_scopes
