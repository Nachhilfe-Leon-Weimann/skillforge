from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import (
    ApplicationClient,
    ApplicationClientScopeGrant,
    ApplicationClientStatus,
)

from ..audit import AuditEventType, write_auth_audit_log
from .errors import ApplicationClientAlreadyExistsError, ApplicationClientNotFoundError


async def list_application_clients(session: AsyncSession) -> list[ApplicationClient]:
    result = await session.execute(
        select(ApplicationClient)
        .order_by(ApplicationClient.client_id)
        .options(
            selectinload(ApplicationClient.secrets),
            selectinload(ApplicationClient.scope_grants).selectinload(ApplicationClientScopeGrant.permission_scope),
        )
    )
    return list(result.scalars().all())


async def get_application_client(session: AsyncSession, *, client_id: str) -> ApplicationClient:
    client = await find_application_client(session, client_id)
    if client is None:
        raise ApplicationClientNotFoundError("Application client not found")

    return client


async def create_application_client(
    session: AsyncSession,
    *,
    client_id: str,
    name: str,
    description: str | None = None,
) -> ApplicationClient:
    if await find_application_client(session, client_id) is not None:
        raise ApplicationClientAlreadyExistsError("Application client already exists")

    client = ApplicationClient(
        client_id=client_id,
        name=name,
        description=description,
        status=ApplicationClientStatus.ACTIVE,
    )
    session.add(client)
    await session.flush()
    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type=AuditEventType.APPLICATION_CLIENT_CREATED,
        success=True,
        detail=f"Created application client {client.client_id}.",
    )

    return await get_application_client(session, client_id=client.client_id)


async def update_application_client(
    session: AsyncSession,
    *,
    client_id: str,
    name: str | None = None,
    description: str | None = None,
    update_description: bool = False,
    status: ApplicationClientStatus | None = None,
) -> ApplicationClient:
    client = await get_application_client(session, client_id=client_id)
    previous_status = client.status

    if name is not None:
        client.name = name
    if update_description:
        client.description = description
    if status is not None:
        client.status = status

    await session.flush()
    event_type = AuditEventType.APPLICATION_CLIENT_UPDATED
    if previous_status != ApplicationClientStatus.DISABLED and client.status == ApplicationClientStatus.DISABLED:
        event_type = AuditEventType.APPLICATION_CLIENT_DISABLED

    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type=event_type,
        success=True,
        detail=f"Updated application client {client.client_id}.",
    )

    return await get_application_client(session, client_id=client.client_id)


async def find_application_client(session: AsyncSession, client_id: str) -> ApplicationClient | None:
    result = await session.execute(
        select(ApplicationClient)
        .where(ApplicationClient.client_id == client_id)
        .options(
            selectinload(ApplicationClient.secrets),
            selectinload(ApplicationClient.scope_grants).selectinload(ApplicationClientScopeGrant.permission_scope),
        )
    )

    return result.scalar_one_or_none()
