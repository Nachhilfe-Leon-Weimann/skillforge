"""User sessions: one row per login of a person through a client (user-authentication spec, decision L),
and ending them.

Revoking acts on the session only: the access tokens already handed out stay valid until they expire.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import UserSession

from ..audit import Actor, AuditEventType, write_user_account_audit_log
from .accounts import get_user_account


class SessionRevokedReason(StrEnum):
    """Why a session ended: the values of ``user_session.revoked_reason``."""

    LOGOUT = "logout"
    PASSWORD_RESET = "password_reset"
    ACCOUNT_DISABLED = "account_disabled"
    ADMIN = "admin"
    REUSE_DETECTED = "reuse_detected"


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID, *, actor: Actor) -> int:
    """Revoke every live session of the account on an administrator's request; return how many."""
    await get_user_account(session, user_id)
    return await revoke_sessions(session, user_id, reason=SessionRevokedReason.ADMIN, actor=actor)


async def revoke_sessions(
    session: AsyncSession, user_id: uuid.UUID, *, reason: SessionRevokedReason, actor: Actor
) -> int:
    """Revoke the live sessions of the account and return how many there were.

    A session that is already revoked keeps its reason and timestamp - the first revocation is the one
    that happened - and an expired one is left as it is. Revoking at least one writes ``session.revoked``,
    revoking none records nothing.
    """
    now = datetime.now(UTC)
    statement = (
        update(UserSession)
        .where(
            UserSession.user_account_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .values(revoked_at=now, revoked_reason=reason)
    )
    # ``execute`` is typed as ``Result``; a Core UPDATE always yields a ``CursorResult`` with ``rowcount``.
    revoked = cast(CursorResult, await session.execute(statement)).rowcount
    if revoked:
        await write_user_account_audit_log(
            session, user_id, AuditEventType.SESSION_REVOKED, f"Revoked {revoked} session(s) ({reason})", actor=actor
        )

    return revoked
