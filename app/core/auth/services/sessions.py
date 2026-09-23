"""User sessions: one row per login (decision K), and ending them.

Revoking acts on the session only: the access tokens already handed out stay valid until they
expire. The ``password`` and ``refresh_token`` grants of P0-6 open and rotate sessions here.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast

from sqlalchemy import CursorResult, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import UserSession

from ..audit import AuditEventType, write_user_account_audit_log
from .accounts import get_user_account


class SessionRevokedReason(StrEnum):
    """Why a session ended: the values of ``user_session.revoked_reason``."""

    LOGOUT = "logout"
    PASSWORD_RESET = "password_reset"
    ACCOUNT_DISABLED = "account_disabled"
    ADMIN = "admin"
    REUSE_DETECTED = "reuse_detected"


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID, *, actor: str) -> int:
    """Revoke every live session of the account on an administrator's request.

    Returns how many were revoked. The access tokens already handed out stay valid until they
    expire (decision K).
    """
    await get_user_account(session, user_id)
    revoked = await revoke_sessions(session, user_id, reason=SessionRevokedReason.ADMIN)
    if revoked:
        await write_user_account_audit_log(
            session, user_id, AuditEventType.SESSION_REVOKED, f"Revoked {revoked} session(s) by {actor}."
        )

    return revoked


async def revoke_sessions(session: AsyncSession, user_id: uuid.UUID, *, reason: SessionRevokedReason) -> int:
    """Revoke the live sessions of the account and return how many there were.

    A session that is already revoked keeps its reason and its timestamp: the first revocation is
    the one that happened.
    """
    statement = (
        update(UserSession)
        .where(UserSession.user_account_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    # execute() is typed as Result, but a Core UPDATE always yields a CursorResult with rowcount.
    result = cast(CursorResult, await session.execute(statement))
    return result.rowcount
