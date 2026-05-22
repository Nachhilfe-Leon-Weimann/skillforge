from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClient, ApplicationClientScopeGrant, PermissionScope

from ..audit import AuditEventType, write_auth_audit_log
from ..scopes import DEFAULT_SCOPES, Scope
from .clients import get_application_client
from .errors import ApplicationClientScopeGrantNotFoundError, InvalidClientScopeError


async def seed_default_scopes(session: AsyncSession) -> list[PermissionScope]:
    scopes: list[PermissionScope] = []
    for scope, description in DEFAULT_SCOPES.items():
        permission_scope = await session.get(PermissionScope, scope.value)
        if permission_scope is None:
            permission_scope = PermissionScope(
                key=scope.value,
                description=description,
                active=True,
            )
            session.add(permission_scope)
        else:
            permission_scope.description = description
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
    scope_keys = normalize_scope_set(scope.value if isinstance(scope, Scope) else scope for scope in scopes)
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
    requested_scopes: Iterable[str] | str | None,
    granted_scopes: frozenset[str],
) -> frozenset[str]:
    normalized_requested_scopes = normalize_scope_set(requested_scopes)
    if not normalized_requested_scopes:
        if not granted_scopes:
            raise InvalidClientScopeError("Client has no active scope grants")
        return granted_scopes

    missing_scopes = normalized_requested_scopes - granted_scopes
    if missing_scopes:
        raise InvalidClientScopeError("Requested scopes are not granted")

    return normalized_requested_scopes


def normalize_scope_set(scopes: Iterable[str] | str | None) -> frozenset[str]:
    if scopes is None:
        return frozenset()

    if isinstance(scopes, str):
        return frozenset(scopes.split())

    return frozenset(str(scope).strip() for scope in scopes if str(scope).strip())
