from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClient, ApplicationClientStatus

from ..audit import AuditEventType, write_auth_audit_log
from ..results import BootstrappedApplicationClient
from ..scopes import Scope
from .clients import _get_application_client
from .scopes import _grant_client_scopes, _normalize_scope_set, seed_default_scopes
from .secrets import _client_has_usable_secret, create_client_secret


async def bootstrap_application_client(
    session: AsyncSession,
    *,
    client_id: str,
    name: str,
    description: str | None,
    scopes: Iterable[Scope | str],
) -> BootstrappedApplicationClient:
    await seed_default_scopes(session)

    client = await _get_application_client(session, client_id)
    created_client = False
    if client is None:
        client = ApplicationClient(
            client_id=client_id,
            name=name,
            description=description,
            status=ApplicationClientStatus.ACTIVE,
        )
        session.add(client)
        await session.flush()
        created_client = True
        await write_auth_audit_log(
            session,
            principal_type="application",
            principal_id=client.id,
            event_type=AuditEventType.APPLICATION_CLIENT_CREATED,
            success=True,
            detail=f"Created application client {client.client_id}",
        )
    else:
        client.name = name
        client.description = description
        client.status = ApplicationClientStatus.ACTIVE

    requested_scope_keys = _normalize_scope_set(scope.value if isinstance(scope, Scope) else scope for scope in scopes)
    granted_scope_keys = await _grant_client_scopes(session, client=client, scope_keys=requested_scope_keys)

    created_secret = None
    if not await _client_has_usable_secret(session, client_id=client.id, now=datetime.now(UTC)):
        created_secret = await create_client_secret(
            session,
            application_client_id=client.id,
            label="bootstrap",
        )

    await session.flush()
    return BootstrappedApplicationClient(
        client=client,
        created_client=created_client,
        created_secret=created_secret,
        granted_scopes=granted_scope_keys,
    )
