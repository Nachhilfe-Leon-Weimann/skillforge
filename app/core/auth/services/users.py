"""User accounts: invitation, administration and the redemption of a one-time token.

An account belongs to exactly one person party and is created by invitation only (ADR 0008,
decision C). One-time tokens are stored as a SHA-256 hash; the plaintext exists once, in the
result of the call that issues it, and never reaches a log or an audit ``detail``.
"""

import hashlib
import secrets as random_secrets
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import (
    Party,
    PartyType,
    UserAccount,
    UserAccountRole,
    UserAccountRoleName,
    UserAccountStatus,
    UserActionToken,
    UserActionTokenPurpose,
    UserSession,
)

from ..audit import AuditEventType, write_auth_audit_log
from ..config import AuthSettings
from ..passwords import hash_password, meets_password_policy
from ..results import IssuedActionToken, UserAccountWithRoles
from ..roles import Role
from .errors import (
    AccountPartyNotAPersonError,
    AccountPartyNotFoundError,
    InvalidActionTokenError,
    UserAccountAlreadyExistsError,
    UserAccountNotFoundError,
    UserAccountStateError,
    UserEmailAlreadyInUseError,
    UserRoleNotFoundError,
    WeakPasswordError,
)
from .roles import derive_roles, derive_roles_for

ACTION_TOKEN_PREFIX = "sf_ua_"
ACTION_TOKEN_BYTES = 32

# The values ``user_session.revoked_reason`` takes here. ``logout`` and ``reuse_detected`` belong
# to the grants of P0-6.
REVOKED_BY_ADMIN = "admin"
REVOKED_ACCOUNT_DISABLED = "account_disabled"
REVOKED_PASSWORD_RESET = "password_reset"


def normalize_email(email: str) -> str:
    """The stored form of a login e-mail address: trimmed and lowercased (decision D).

    A fixed point, and the form the ``ck_user_account_email_lowercase`` check constraint accepts.
    """
    return email.strip().lower()


def hash_action_token(plaintext: str) -> str:
    """The stored form of a one-time token: SHA-256 hex.

    Not Argon2: the token has full entropy, and the lookup needs a deterministic hash.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


async def invite_user_account(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    party_id: uuid.UUID,
    email: str,
    roles: Iterable[UserAccountRoleName] = (),
    actor: str,
) -> tuple[UserAccountWithRoles, IssuedActionToken]:
    """Create an ``invited`` account for a person party and issue its invitation token."""
    party = await session.get(Party, party_id)
    if party is None:
        raise AccountPartyNotFoundError(f"No party with id {party_id}")
    if party.type is not PartyType.PERSON:
        raise AccountPartyNotAPersonError(f"Party {party_id} is a company")
    if await find_user_account_by_party(session, party_id) is not None:
        raise UserAccountAlreadyExistsError(f"Party {party_id} already has a user account")

    normalized = normalize_email(email)
    await _require_email_unused(session, normalized)

    account = UserAccount(
        id=uuid.uuid4(),
        party_id=party_id,
        email=normalized,
        status=UserAccountStatus.INVITED,
        roles=[UserAccountRole(role=role) for role in sorted(set(roles))],
    )
    session.add(account)
    await session.flush()
    await _audit(
        session,
        account.id,
        AuditEventType.USER_ACCOUNT_INVITED,
        f"Invited user account for party {party_id} by {actor}.",
    )

    invitation = await issue_action_token(
        session,
        settings,
        user_id=account.id,
        purpose=UserActionTokenPurpose.INVITATION,
        actor=actor,
    )
    return await load_user_account(session, account.id), invitation


async def get_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccount:
    """Return the account behind ``user_id``, with its stored roles loaded.

    A ``select`` rather than ``session.get``: the latter answers from the identity map without
    applying the loader option, and the unloaded collection would then be read after the response
    has left the session.
    """
    account = await session.scalar(
        select(UserAccount).where(UserAccount.id == user_id).options(selectinload(UserAccount.roles))
    )
    if account is None:
        raise UserAccountNotFoundError(f"No user account with id {user_id}")

    return account


async def load_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccountWithRoles:
    """Return the account behind ``user_id`` together with its stored *and* derived roles."""
    account = await get_user_account(session, user_id)
    return UserAccountWithRoles(
        account=account,
        roles=_stored_roles(account) | await derive_roles(session, account.party_id),
    )


async def list_user_accounts(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    status: UserAccountStatus | None = None,
    party_id: uuid.UUID | None = None,
    email: str | None = None,
) -> tuple[list[UserAccountWithRoles], int]:
    """Return one page of accounts ordered by the unique ``email``, plus the total count.

    ``email`` matches exactly, against the normalized form; the filters never fail, a combination
    nothing matches is an empty page.
    """
    conditions = []
    if status is not None:
        conditions.append(UserAccount.status == status)
    if party_id is not None:
        conditions.append(UserAccount.party_id == party_id)
    if email is not None:
        conditions.append(UserAccount.email == normalize_email(email))

    total = await session.scalar(select(func.count()).select_from(UserAccount).where(*conditions))
    result = await session.execute(
        select(UserAccount)
        .where(*conditions)
        .order_by(UserAccount.email)
        .limit(limit)
        .offset(offset)
        .options(selectinload(UserAccount.roles))
    )
    accounts = list(result.scalars())
    derived = await derive_roles_for(session, (account.party_id for account in accounts))
    page = [
        UserAccountWithRoles(account=account, roles=_stored_roles(account) | derived[account.party_id])
        for account in accounts
    ]
    return page, total or 0


async def update_user_account(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    email: str | None = None,
    status: UserAccountStatus | None = None,
    actor: str,
) -> UserAccountWithRoles:
    """Change the given fields; ``None`` means unchanged (both columns are ``NOT NULL``).

    Disabling the account revokes its sessions. An account without a password cannot be activated:
    it has never proven that anybody holds its credential.
    """
    account = await get_user_account(session, user_id)
    previous_status = account.status

    if email is not None:
        normalized = normalize_email(email)
        if normalized != account.email:
            await _require_email_unused(session, normalized)
            account.email = normalized

    if status is not None and status is not previous_status:
        if status is UserAccountStatus.ACTIVE and account.password_hash is None:
            raise UserAccountStateError(f"User account {user_id} has no password")
        account.status = status
        if status is UserAccountStatus.DISABLED:
            await _revoke_sessions(session, user_id, reason=REVOKED_ACCOUNT_DISABLED)

    await session.flush()
    await _audit(session, user_id, _update_event(previous_status, account.status), f"Updated user account by {actor}.")
    return await load_user_account(session, user_id)


async def add_user_role(
    session: AsyncSession, user_id: uuid.UUID, *, role: UserAccountRoleName, actor: str
) -> UserAccountWithRoles:
    """Give the account a stored role. Idempotent: holding it already changes nothing.

    The account row is locked first, so two overlapping requests cannot both insert the same
    primary key.
    """
    await _lock_user_account(session, user_id)
    account = await get_user_account(session, user_id)
    if role not in {stored.role for stored in account.roles}:
        account.roles.append(UserAccountRole(role=role))
        await session.flush()
        await _audit(session, user_id, AuditEventType.USER_ROLE_ADDED, f"Added role {role.value} by {actor}.")

    return await load_user_account(session, user_id)


async def remove_user_role(session: AsyncSession, user_id: uuid.UUID, *, role: UserAccountRoleName, actor: str) -> None:
    """Take a stored role away; a role the account does not hold is a ``UserRoleNotFoundError``."""
    account = await get_user_account(session, user_id)
    stored = next((held for held in account.roles if held.role is role), None)
    if stored is None:
        raise UserRoleNotFoundError(f"User account {user_id} does not hold the role {role.value}")

    account.roles.remove(stored)
    await session.flush()
    await _audit(session, user_id, AuditEventType.USER_ROLE_REMOVED, f"Removed role {role.value} by {actor}.")


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
    account = await _lock_user_account(session, user_id)
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

    plaintext = f"{ACTION_TOKEN_PREFIX}{random_secrets.token_urlsafe(ACTION_TOKEN_BYTES)}"
    token = UserActionToken(
        id=uuid.uuid4(),
        user_account_id=user_id,
        purpose=purpose,
        token_hash=hash_action_token(plaintext),
        expires_at=now + timedelta(hours=_expire_hours(settings, purpose)),
        issued_by=actor,
    )
    session.add(token)
    await session.flush()
    await _audit(session, user_id, _issue_event(purpose), f"Issued a {purpose.value} token by {actor}.")

    return IssuedActionToken(plaintext=plaintext, token=token)


async def redeem_action_token(session: AsyncSession, *, plaintext: str, new_password: str, actor: str) -> UserAccount:
    """Set the account's password from a one-time token, whatever its purpose.

    An ``invited`` account becomes ``active``; a ``disabled`` one stays disabled. The counter and
    the lock are cleared, the token is marked used, and a ``password_reset`` revokes every session
    of the account - an invitation has none to revoke.
    """
    token = await session.scalar(
        select(UserActionToken).where(UserActionToken.token_hash == hash_action_token(plaintext))
    )
    now = datetime.now(UTC)
    if token is None or token.used_at is not None or token.invalidated_at is not None or token.expires_at <= now:
        raise InvalidActionTokenError("Unknown, used or expired token")
    if not meets_password_policy(new_password):
        raise WeakPasswordError(WeakPasswordError.message)

    account = await _lock_user_account(session, token.user_account_id)
    account.password_hash = hash_password(new_password)
    account.failed_login_count = 0
    account.locked_until = None
    token.used_at = now

    activated = account.status is UserAccountStatus.INVITED
    if activated:
        account.status = UserAccountStatus.ACTIVE
    if token.purpose is UserActionTokenPurpose.PASSWORD_RESET:
        await _revoke_sessions(session, account.id, reason=REVOKED_PASSWORD_RESET)

    await session.flush()
    await _audit(session, account.id, AuditEventType.PASSWORD_SET, f"Set the password via {token.purpose.value}.")
    if activated:
        await _audit(session, account.id, AuditEventType.USER_ACCOUNT_ACTIVATED, f"Activated by {actor}.")

    return account


async def revoke_user_sessions(session: AsyncSession, user_id: uuid.UUID, *, actor: str) -> int:
    """Revoke every live session of the account on an administrator's request.

    Returns how many were revoked. The access tokens already handed out stay valid until they
    expire (decision K).
    """
    await get_user_account(session, user_id)
    revoked = await _revoke_sessions(session, user_id, reason=REVOKED_BY_ADMIN)
    if revoked:
        await _audit(session, user_id, AuditEventType.SESSION_REVOKED, f"Revoked {revoked} session(s) by {actor}.")

    return revoked


async def _revoke_sessions(session: AsyncSession, user_id: uuid.UUID, *, reason: str) -> int:
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


async def _lock_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccount:
    """Return the account with its row locked until the transaction ends."""
    account = await session.scalar(select(UserAccount).where(UserAccount.id == user_id).with_for_update())
    if account is None:
        raise UserAccountNotFoundError(f"No user account with id {user_id}")

    return account


async def find_user_account_by_party(session: AsyncSession, party_id: uuid.UUID) -> UserAccount | None:
    """Return the account of the party, or ``None``: a party has at most one (decision C)."""
    return await session.scalar(select(UserAccount).where(UserAccount.party_id == party_id))


async def _require_email_unused(session: AsyncSession, email: str) -> None:
    if await session.scalar(select(UserAccount.id).where(UserAccount.email == email)) is not None:
        raise UserEmailAlreadyInUseError("The e-mail address is already in use")


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


def _update_event(previous: UserAccountStatus, current: UserAccountStatus) -> AuditEventType:
    if previous is not current and current is UserAccountStatus.DISABLED:
        return AuditEventType.USER_ACCOUNT_DISABLED
    if previous is UserAccountStatus.DISABLED and current is not UserAccountStatus.DISABLED:
        return AuditEventType.USER_ACCOUNT_ENABLED

    return AuditEventType.USER_ACCOUNT_UPDATED


def _stored_roles(account: UserAccount) -> frozenset[Role]:
    return frozenset(Role(stored.role.value) for stored in account.roles)


async def _audit(session: AsyncSession, user_id: uuid.UUID, event_type: AuditEventType, detail: str) -> None:
    """Record what happened to the account. ``detail`` never carries a secret or an e-mail address."""
    await write_auth_audit_log(
        session,
        # The subject of the entry, like the client services record the client they changed.
        principal_type="user",
        principal_id=user_id,
        event_type=event_type,
        success=True,
        detail=detail,
    )
