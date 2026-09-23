import uuid
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import AuthAuditLog

from .principal import PrincipalType


class AuditEventType(StrEnum):
    """What an ``AuthAuditLog`` entry records.

    A ``detail`` never carries a plaintext secret, and never an e-mail address typed for an account
    that does not exist (spec: security rules).
    """

    APPLICATION_CLIENT_CREATED = "application_client.created"
    APPLICATION_CLIENT_DISABLED = "application_client.disabled"
    APPLICATION_CLIENT_UPDATED = "application_client.updated"
    CLIENT_SECRET_CREATED = "client_secret.created"
    CLIENT_SECRET_REVOKED = "client_secret.revoked"
    SCOPE_GRANT_ADDED = "scope_grant.added"
    SCOPE_GRANT_REMOVED = "scope_grant.removed"
    TOKEN_DENIED = "token.denied"
    TOKEN_ISSUED = "token.issued"
    USER_ACCOUNT_INVITED = "user_account.invited"
    USER_ACCOUNT_ACTIVATED = "user_account.activated"
    USER_ACCOUNT_UPDATED = "user_account.updated"
    USER_ACCOUNT_DISABLED = "user_account.disabled"
    USER_ACCOUNT_ENABLED = "user_account.enabled"
    USER_ROLE_ADDED = "user_role.added"
    USER_ROLE_REMOVED = "user_role.removed"
    INVITATION_ISSUED = "invitation.issued"
    PASSWORD_RESET_ISSUED = "password_reset.issued"
    PASSWORD_SET = "password.set"
    SESSION_REVOKED = "session.revoked"
    SESSION_REUSE_DETECTED = "session.reuse_detected"


async def write_auth_audit_log(
    session: AsyncSession,
    *,
    principal_type: PrincipalType | None,
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


async def write_user_account_audit_log(
    session: AsyncSession,
    user_id: uuid.UUID,
    event_type: AuditEventType,
    detail: str,
) -> None:
    """Record what happened to a user account. ``detail`` never carries a secret or an e-mail address.

    The account is the subject of the entry, the way the client services record the client they
    changed; who asked is part of ``detail``.
    """
    await write_auth_audit_log(
        session,
        principal_type=PrincipalType.USER,
        principal_id=user_id,
        event_type=event_type,
        success=True,
        detail=detail,
    )
