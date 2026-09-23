"""One-time tokens: issuing an invitation or a password reset, and redeeming either.

A token is stored as its ``digest`` only; the plaintext exists once, in the result of the call that
issues it, and never reaches a log or an audit ``detail``.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import UserAccount, UserAccountStatus, UserActionToken, UserActionTokenPurpose

from ..audit import AuditEventType, write_user_account_audit_log
from ..config import AuthSettings
from ..passwords import meets_password_policy
from ..results import IssuedActionToken
from ..secrets import digest, generate_secret, hash_secret
from .accounts import lock_user_account
from .errors import InvalidActionTokenError, UserAccountStateError, WeakPasswordError
from .sessions import SessionRevokedReason, revoke_sessions

ACTION_TOKEN_PREFIX = "sf_ua_"


async def issue_action_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    user_id: uuid.UUID,
    purpose: UserActionTokenPurpose,
    actor: str,
) -> IssuedActionToken:
    """Issue a one-time token and invalidate the account's earlier unused ones of that purpose.

    The account row is locked for the whole operation: two requests arriving together would
    otherwise both invalidate what they found and then both insert, leaving two live tokens.
    """
    account = await lock_user_account(session, user_id)
    _require_purpose_allowed(account, purpose)

    now = datetime.now(UTC)
    await session.execute(
        update(UserActionToken)
        .where(
            UserActionToken.user_account_id == user_id,
            UserActionToken.purpose == purpose,
            UserActionToken.used_at.is_(None),
            UserActionToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=now)
    )

    plaintext = generate_secret(ACTION_TOKEN_PREFIX)
    token = UserActionToken(
        id=uuid.uuid4(),
        user_account_id=user_id,
        purpose=purpose,
        token_hash=digest(plaintext),
        expires_at=now + timedelta(hours=_expire_hours(settings, purpose)),
        issued_by=actor,
    )
    session.add(token)
    await session.flush()
    await write_user_account_audit_log(
        session, user_id, _issue_event(purpose), f"Issued a {purpose.value} token by {actor}."
    )

    return IssuedActionToken(plaintext=plaintext, token=token)


async def redeem_action_token(session: AsyncSession, *, plaintext: str, new_password: str, actor: str) -> UserAccount:
    """Set the account's password from a one-time token, whatever its purpose.

    An ``invited`` account becomes ``active``; a ``disabled`` one stays disabled. The counter and
    the lock are cleared, the token is marked used, and a ``password_reset`` revokes every session
    of the account - an invitation has none to revoke.
    """
    found = await session.scalar(select(UserActionToken).where(UserActionToken.token_hash == digest(plaintext)))
    # The cheap look-up first: a token that was never live costs neither a hash nor a row lock.
    if found is None or not _is_live(found, datetime.now(UTC)):
        raise InvalidActionTokenError("Unknown, used or expired token")
    if not meets_password_policy(new_password):
        raise WeakPasswordError(WeakPasswordError.message)

    # Hashed before the lock is taken: Argon2 is slow by design, and every other issue or redeem
    # on this account would queue behind it. The row lock still decides whether it gets stored.
    password_hash = hash_secret(new_password)

    account = await lock_user_account(session, found.user_account_id)
    token = await _lock_action_token(session, found.id)
    # Only now is the answer authoritative: the look-up above was not serialized against a redeem
    # or an issue that was running at the same time, and either may have spent this token since.
    now = datetime.now(UTC)
    if token is None or not _is_live(token, now):
        raise InvalidActionTokenError("Unknown, used or expired token")

    account.password_hash = password_hash
    account.failed_login_count = 0
    account.locked_until = None
    token.used_at = now

    activated = account.status is UserAccountStatus.INVITED
    if activated:
        account.status = UserAccountStatus.ACTIVE
    if token.purpose is UserActionTokenPurpose.PASSWORD_RESET:
        await revoke_sessions(session, account.id, reason=SessionRevokedReason.PASSWORD_RESET)

    await session.flush()
    await write_user_account_audit_log(
        session, account.id, AuditEventType.PASSWORD_SET, f"Set the password via {token.purpose.value}."
    )
    if activated:
        await write_user_account_audit_log(
            session, account.id, AuditEventType.USER_ACCOUNT_ACTIVATED, f"Activated by {actor}."
        )

    return account


async def _lock_action_token(session: AsyncSession, token_id: uuid.UUID) -> UserActionToken | None:
    """Re-read the token row with its own lock, past whatever the session still holds of it."""
    return await session.scalar(
        select(UserActionToken)
        .where(UserActionToken.id == token_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _is_live(token: UserActionToken, now: datetime) -> bool:
    """Whether the token can still be redeemed: not used, not replaced, not expired."""
    return token.used_at is None and token.invalidated_at is None and token.expires_at > now


def _require_purpose_allowed(account: UserAccount, purpose: UserActionTokenPurpose) -> None:
    """An invitation is for an account without a password, a reset for one that has one."""
    has_password = account.password_hash is not None
    if purpose is UserActionTokenPurpose.INVITATION and has_password:
        raise UserAccountStateError(f"User account {account.id} already has a password")
    if purpose is UserActionTokenPurpose.PASSWORD_RESET and not has_password:
        raise UserAccountStateError(f"User account {account.id} has no password yet")


def _expire_hours(settings: AuthSettings, purpose: UserActionTokenPurpose) -> int:
    match purpose:
        case UserActionTokenPurpose.INVITATION:
            return settings.invitation_expire_hours
        case UserActionTokenPurpose.PASSWORD_RESET:
            return settings.password_reset_expire_hours


def _issue_event(purpose: UserActionTokenPurpose) -> AuditEventType:
    match purpose:
        case UserActionTokenPurpose.INVITATION:
            return AuditEventType.INVITATION_ISSUED
        case UserActionTokenPurpose.PASSWORD_RESET:
            return AuditEventType.PASSWORD_RESET_ISSUED
