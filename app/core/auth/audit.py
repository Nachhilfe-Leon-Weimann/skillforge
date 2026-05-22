import uuid
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import AuthAuditLog


class AuditEventType(StrEnum):
    APPLICATION_CLIENT_CREATED = "application_client.created"
    APPLICATION_CLIENT_DISABLED = "application_client.disabled"
    APPLICATION_CLIENT_UPDATED = "application_client.updated"
    CLIENT_SECRET_CREATED = "client_secret.created"
    CLIENT_SECRET_REVOKED = "client_secret.revoked"
    SCOPE_GRANT_ADDED = "scope_grant.added"
    SCOPE_GRANT_REMOVED = "scope_grant.removed"
    TOKEN_DENIED = "token.denied"
    TOKEN_ISSUED = "token.issued"


async def write_auth_audit_log(
    session: AsyncSession,
    *,
    principal_type: str | None,
    principal_id: str | uuid.UUID | None,
    event_type: str,
    success: bool,
    detail: str | None = None,
) -> None:
    session.add(
        AuthAuditLog(
            principal_type=principal_type,
            principal_id=str(principal_id) if principal_id is not None else None,
            event_type=event_type,
            success=success,
            detail=detail,
        )
    )
    await session.flush()
