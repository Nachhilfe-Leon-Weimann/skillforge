"""User sessions: one row per login of a person through a client (user-authentication spec, decision L) -
opening, rotating and ending them.

A session's refresh token is opaque and rotates on every refresh; the session stores the digests of the
current one and of the one it replaced. Revoking acts on the session only: the access tokens already
handed out stay valid until they expire.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast

from sqlalchemy import CursorResult, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import UserSession

from ..audit import Actor, AuditEventType, write_user_account_audit_log
from ..principal import ApplicationPrincipal
from ..scopes import format_scopes
from ..secrets import digest, generate_secret
from .accounts import get_user_account

REFRESH_TOKEN_PREFIX = "sf_rt_"

REFRESH_REUSE_GRACE = timedelta(seconds=10)
"""How long a rotated-out refresh token is taken for a race rather than a replay (decision L).

Two refreshes of one token that overlap get one rotation; the loser presents a token that was current a
moment ago and is only refused. Later, the same token means someone kept a copy: the session ends.
"""


class SessionRevokedReason(StrEnum):
    """Why a session ended: the values of ``user_session.revoked_reason``."""

    LOGOUT = "logout"
    PASSWORD_RESET = "password_reset"
    ACCOUNT_DISABLED = "account_disabled"
    ADMIN = "admin"
    REUSE_DETECTED = "reuse_detected"


class RefreshTokenState(StrEnum):
    """What a presented refresh token is to the session it names (``refresh_token_state``)."""

    CURRENT = "current"
    """The live session's current token: it may be rotated."""
    RACED = "raced"
    """The live session's previous token, rotated out within ``REFRESH_REUSE_GRACE``: two refreshes raced."""
    REUSED = "reused"
    """The live session's previous token, rotated out before the grace: a replay."""
    ENDED = "ended"
    """A token of a revoked or expired session."""


@dataclass(frozen=True)
class OpenedSession:
    """A new session together with its first refresh token - the only place that plaintext exists."""

    row: UserSession
    refresh_token: str


async def open_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    application_client_id: uuid.UUID,
    scope: frozenset[str],
    expires_at: datetime,
) -> OpenedSession:
    """Open a session of the account through the client; ``scope`` becomes the ceiling of every refresh."""
    refresh_token = generate_secret(REFRESH_TOKEN_PREFIX)
    row = UserSession(
        user_account_id=user_id,
        application_client_id=application_client_id,
        scope=format_scopes(scope),
        refresh_token_hash=digest(refresh_token),
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return OpenedSession(row=row, refresh_token=refresh_token)


async def lock_session_by_refresh_token(
    session: AsyncSession, refresh_token: str, *, application_client_id: uuid.UUID
) -> UserSession | None:
    """Return the session of this client whose current *or* previous refresh token is ``refresh_token``, locked.

    One statement against both digests, so a refresh that waits for a concurrent rotation still finds the
    session - now by its previous token. ``populate_existing`` makes the checks after the lock read what the
    database holds. Another client's session is not found: only the client that opened it may use it.
    """
    token_digest = digest(refresh_token)
    return await session.scalar(
        select(UserSession)
        .where(
            UserSession.application_client_id == application_client_id,
            or_(
                UserSession.refresh_token_hash == token_digest,
                UserSession.previous_refresh_token_hash == token_digest,
            ),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def refresh_token_state(user_session: UserSession, refresh_token: str, *, now: datetime) -> RefreshTokenState:
    """Classify ``refresh_token`` - one ``lock_session_by_refresh_token`` found ``user_session`` by."""
    if user_session.revoked_at is not None or user_session.expires_at <= now:
        return RefreshTokenState.ENDED
    if user_session.refresh_token_hash == digest(refresh_token):
        return RefreshTokenState.CURRENT
    if user_session.rotated_at is not None and now - user_session.rotated_at <= REFRESH_REUSE_GRACE:
        return RefreshTokenState.RACED

    return RefreshTokenState.REUSED


def rotate_session(user_session: UserSession, *, now: datetime) -> str:
    """Replace the session's refresh token and return the new one; the old one becomes ``previous``."""
    refresh_token = generate_secret(REFRESH_TOKEN_PREFIX)
    user_session.previous_refresh_token_hash = user_session.refresh_token_hash
    user_session.refresh_token_hash = digest(refresh_token)
    user_session.rotated_at = now
    user_session.last_used_at = now
    return refresh_token


async def revoke_session(
    session: AsyncSession, user_session: UserSession, *, reason: SessionRevokedReason, actor: Actor, now: datetime
) -> None:
    """End one session; a detected reuse is recorded as ``session.reuse_detected``, anything else as
    ``session.revoked``."""
    user_session.revoked_at = now
    user_session.revoked_reason = reason
    event = (
        AuditEventType.SESSION_REUSE_DETECTED
        if reason is SessionRevokedReason.REUSE_DETECTED
        else AuditEventType.SESSION_REVOKED
    )
    await write_user_account_audit_log(
        session, user_session.user_account_id, event, f"Revoked session {user_session.id} ({reason})", actor=actor
    )


async def revoke_session_by_refresh_token(
    session: AsyncSession, *, refresh_token: str, client: ApplicationPrincipal
) -> None:
    """Log out: end the live session ``refresh_token`` belongs to, if ``client`` opened it (RFC 7009).

    Finding none is no error - the caller answers the same either way. A rotated-out token names its
    session as well, so a logout with a stale token still ends it.
    """
    now = datetime.now(UTC)
    found = await lock_session_by_refresh_token(session, refresh_token, application_client_id=client.principal_id)
    if found is not None and refresh_token_state(found, refresh_token, now=now) is not RefreshTokenState.ENDED:
        await revoke_session(session, found, reason=SessionRevokedReason.LOGOUT, actor=client, now=now)


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
