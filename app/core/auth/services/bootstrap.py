from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClient, ApplicationClientStatus, GrantMode

from ..audit import AuditEventType, write_auth_audit_log
from ..results import BootstrappedApplicationClient
from ..scopes import Scope, parse_scopes
from .clients import find_application_client
from .scopes import grant_client_scopes, seed_default_scopes
from .secrets import client_has_usable_secret, create_client_secret


async def bootstrap_application_client(
    session: AsyncSession,
    *,
    client_id: str,
    scopes: Iterable[Scope | str],
    name: str | None = None,
    description: str | None = None,
    mode: GrantMode = GrantMode.APPLICATION,
) -> BootstrappedApplicationClient:
    """Ensure an active client ``client_id`` holding ``scopes`` in ``mode`` and a usable secret.

    A missing client is created, named ``name`` or else after its ID. An existing one keeps its
    grants and secrets; it is re-enabled, and takes ``name`` and ``description`` when they are
    given - ``None`` keeps what it has. A change to the client writes one
    ``application_client.updated`` entry, a run that changes nothing writes none.
    """
    await seed_default_scopes(session)

    client = await find_application_client(session, client_id)
    created_client = False
    if client is None:
        client = ApplicationClient(
            client_id=client_id,
            name=client_id if name is None else name,
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
        await _ensure_active(session, client, name=name, description=description)

    requested_scope_keys = parse_scopes(scopes)
    await grant_client_scopes(session, client=client, scope_keys=requested_scope_keys, mode=mode)

    created_secret = None
    if not await client_has_usable_secret(session, client_id=client.id, now=datetime.now(UTC)):
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
        granted_scopes=requested_scope_keys,
    )


async def _ensure_active(
    session: AsyncSession, client: ApplicationClient, *, name: str | None, description: str | None
) -> None:
    """Re-enable ``client`` and set the ``name`` and ``description`` given; record a change in one audit entry."""
    before = (client.name, client.description, client.status)
    if name is not None:
        client.name = name
    if description is not None:
        client.description = description
    client.status = ApplicationClientStatus.ACTIVE
    if (client.name, client.description, client.status) == before:
        return

    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type=AuditEventType.APPLICATION_CLIENT_UPDATED,
        success=True,
        detail=f"Updated application client {client.client_id}.",
    )
