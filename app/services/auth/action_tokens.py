"""One-time tokens: issuing an invitation or a password reset, redeeming either, invalidating them.

A token is stored as its ``digest`` only; the plaintext exists once, in the result of the call that
issues it, and never reaches a log, an audit ``detail`` or an error.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.config import AuthSettings
from app.core.auth.passwords import meets_password_policy
from app.core.auth.secrets import digest, generate_secret, hash_secret_async
from app.core.db.models import UserAccount, UserActionToken, UserActionTokenPurpose

from .accounts import lock_user_account
from .audit import Actor, AuditEventType, format_actor, write_user_account_audit_log
from .errors import InvalidActionTokenError, UserAccountNotFoundError, UserAccountStateError, WeakPasswordError
from .results import IssuedActionToken
from .sessions import SessionRevokedReason, revoke_sessions

ACTION_TOKEN_PREFIX = "sf_ua_"


async def issue_action_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    user_id: uuid.UUID,
    purpose: UserActionTokenPurpose,
    actor: Actor,
) -> IssuedActionToken:
    """Issue a one-time token and invalidate the account's earlier unused ones of that purpose.

    An invitation needs an e-mail address and no password yet, a reset needs a password. The account
    row is locked for the whole operation: two requests arriving together would otherwise both
    invalidate what they found and then both insert, leaving two live tokens.
    """
    account = await lock_user_account(session, user_id)
    _require_issuable(account, purpose)
    await invalidate_action_tokens(session, user_id, purposes=[purpose])

    plaintext = generate_secret(ACTION_TOKEN_PREFIX)
    token = UserActionToken(
        user_account_id=user_id,
        purpose=purpose,
        token_hash=digest(plaintext),
        expires_at=datetime.now(UTC) + timedelta(hours=_expire_hours(settings, purpose)),
        issued_by=format_actor(actor),
    )
    session.add(token)
    await session.flush()
    await write_user_account_audit_log(
        session, user_id, _issued_event(purpose), f"Issued the {_name(purpose)} token", actor=actor
    )

    return IssuedActionToken(plaintext=plaintext, token=token)


async def redeem_action_token(session: AsyncSession, *, plaintext: str, new_password: str, actor: Actor) -> UserAccount:
    """Set the account's password with a one-time token of either purpose.

    Clears the failed-login counter and the lock and marks the token used; a ``password_reset``
    revokes every session of the account. The status does not change: a ``disabled`` account stays
    disabled. A token that is not live - or whose account is gone - is one ``InvalidActionTokenError``.
    """
    found = await session.scalar(select(UserActionToken).where(UserActionToken.token_hash == digest(plaintext)))
    # The cheap look-up first: a token that was never live costs neither a hash nor a row lock.
    if found is None or not _is_live(found, datetime.now(UTC)):
        raise InvalidActionTokenError("Unknown, used, invalidated or expired token")
    if not meets_password_policy(new_password):
        raise WeakPasswordError("The password violates the policy")

    # Hashed before the lock: Argon2 is slow by design, and every other issue or redeem on this
    # account would queue behind it. The re-read under the lock still decides whether it is stored.
    password_hash = await hash_secret_async(new_password)
    try:
        account = await lock_user_account(session, found.user_account_id)
    except UserAccountNotFoundError:
        # The party was deleted since the look-up, and its account and tokens with it.
        raise InvalidActionTokenError("The token's account is gone") from None
    # Only under the lock is the answer authoritative: a redeem or an issue that ran at the same time
    # may have spent or replaced the token since the look-up. The locked account keeps the row alive.
    await session.refresh(found, with_for_update=True)
    now = datetime.now(UTC)
    if not _is_live(found, now):
        raise InvalidActionTokenError("Unknown, used, invalidated or expired token")

    account.password_hash = password_hash
    account.failed_login_count = 0
    account.locked_until = None
    found.used_at = now
    await write_user_account_audit_log(
        session,
        account.id,
        AuditEventType.PASSWORD_SET,
        f"Set the password with the {_name(found.purpose)} token",
        actor=actor,
    )
    if found.purpose is UserActionTokenPurpose.PASSWORD_RESET:
        await revoke_sessions(session, account.id, reason=SessionRevokedReason.PASSWORD_RESET, actor=actor)

    return account


async def invalidate_action_tokens(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    purposes: Iterable[UserActionTokenPurpose] = tuple(UserActionTokenPurpose),
) -> None:
    """Make the account's unused tokens of ``purposes`` stop working; a used token stays as it was."""
    await session.execute(
        update(UserActionToken)
        .where(
            UserActionToken.user_account_id == user_id,
            UserActionToken.purpose.in_(list(purposes)),
            UserActionToken.used_at.is_(None),
            UserActionToken.invalidated_at.is_(None),
        )
        .values(invalidated_at=datetime.now(UTC))
    )


def _is_live(token: UserActionToken, now: datetime) -> bool:
    """Whether the token can still be redeemed: not used, not invalidated, not expired."""
    return token.used_at is None and token.invalidated_at is None and token.expires_at > now


def _require_issuable(account: UserAccount, purpose: UserActionTokenPurpose) -> None:
    """An invitation sets up the password login, so it needs an e-mail address and no password yet; a
    reset needs a password."""
    match purpose:
        case UserActionTokenPurpose.INVITATION if account.email is None:
            raise UserAccountStateError(f"User account {account.id} has no e-mail address")
        case UserActionTokenPurpose.INVITATION if account.password_hash is not None:
            raise UserAccountStateError(f"User account {account.id} already has a password")
        case UserActionTokenPurpose.PASSWORD_RESET if account.password_hash is None:
            raise UserAccountStateError(f"User account {account.id} has no password yet")


def _expire_hours(settings: AuthSettings, purpose: UserActionTokenPurpose) -> int:
    match purpose:
        case UserActionTokenPurpose.INVITATION:
            return settings.invitation_expire_hours
        case UserActionTokenPurpose.PASSWORD_RESET:
            return settings.password_reset_expire_hours


def _issued_event(purpose: UserActionTokenPurpose) -> AuditEventType:
    match purpose:
        case UserActionTokenPurpose.INVITATION:
            return AuditEventType.INVITATION_ISSUED
        case UserActionTokenPurpose.PASSWORD_RESET:
            return AuditEventType.PASSWORD_RESET_ISSUED


def _name(purpose: UserActionTokenPurpose) -> str:
    """The purpose as an audit ``detail`` names it: ``invitation``, ``password reset``."""
    return purpose.replace("_", " ")
