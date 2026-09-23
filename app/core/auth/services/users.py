"""User accounts: invitation and administration.

An account belongs to exactly one person party and is created by invitation only (ADR 0008,
decision C). Its one-time tokens are ``action_tokens``', its sessions ``sessions``'; reading and
locking an account row is ``accounts``'.
"""

import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import (
    Party,
    PartyType,
    UserAccount,
    UserAccountRole,
    UserAccountRoleName,
    UserAccountStatus,
    UserActionTokenPurpose,
)

from ..audit import AuditEventType, write_user_account_audit_log
from ..config import AuthSettings
from ..inputs import normalize_email
from ..results import CreatedUserAccount, UserAccountWithRoles
from ..roles import Role
from .accounts import find_user_account_by_party, get_user_account, lock_user_account
from .action_tokens import issue_action_token
from .errors import (
    AccountPartyNotAPersonError,
    AccountPartyNotFoundError,
    UserAccountAlreadyExistsError,
    UserAccountManagementError,
    UserAccountStateError,
    UserEmailAlreadyInUseError,
    UserRoleNotFoundError,
)
from .roles import derive_roles, derive_roles_for
from .sessions import SessionRevokedReason, revoke_sessions


async def invite_user_account(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    party_id: uuid.UUID,
    email: str,
    roles: Iterable[UserAccountRoleName] = (),
    actor: str,
) -> CreatedUserAccount:
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
    async with _unique_account(session):
        session.add(account)
    await write_user_account_audit_log(
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
    return CreatedUserAccount(view=await load_user_account(session, account.id), invitation=invitation)


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

    A no-op is not a write: an empty body, or one that repeats what the account already says,
    changes nothing and records nothing. Every change that does happen gets its own audit entry.

    Disabling the account revokes its sessions. An account without a password cannot be activated:
    it has never proven that anybody holds its credential.
    """
    account = await get_user_account(session, user_id)
    email_changed = False
    status_changed = False

    if email is not None:
        normalized = normalize_email(email)
        if normalized != account.email:
            await _require_email_unused(session, normalized)
            async with _unique_account(session):
                account.email = normalized
            email_changed = True

    if status is not None and status is not account.status:
        if status is UserAccountStatus.ACTIVE and account.password_hash is None:
            raise UserAccountStateError(f"User account {user_id} has no password")
        account.status = status
        if status is UserAccountStatus.DISABLED:
            await revoke_sessions(session, user_id, reason=SessionRevokedReason.ACCOUNT_DISABLED)
        status_changed = True

    if not (email_changed or status_changed):
        return await load_user_account(session, user_id)

    await session.flush()
    if email_changed:
        # Never the address itself: it is a personal value, and the entry says which account.
        await write_user_account_audit_log(
            session, user_id, AuditEventType.USER_ACCOUNT_UPDATED, f"Changed the e-mail address by {actor}."
        )
    if status_changed:
        await write_user_account_audit_log(
            session, user_id, _status_event(account.status), f"Set the status to {account.status} by {actor}."
        )

    return await load_user_account(session, user_id)


async def add_user_role(
    session: AsyncSession, user_id: uuid.UUID, *, role: UserAccountRoleName, actor: str
) -> UserAccountWithRoles:
    """Give the account a stored role. Idempotent: holding it already changes nothing.

    The account row is locked first, so two overlapping requests cannot both insert the same
    primary key.
    """
    account = await lock_user_account(session, user_id)
    if role not in {stored.role for stored in account.roles}:
        account.roles.append(UserAccountRole(role=role))
        await session.flush()
        await write_user_account_audit_log(
            session, user_id, AuditEventType.USER_ROLE_ADDED, f"Added role {role.value} by {actor}."
        )

    return await load_user_account(session, user_id)


async def remove_user_role(session: AsyncSession, user_id: uuid.UUID, *, role: UserAccountRoleName, actor: str) -> None:
    """Take a stored role away; a role the account does not hold is a ``UserRoleNotFoundError``.

    Locked like ``add_user_role``: two overlapping removals would otherwise both find the row and
    the second would delete what is no longer there.
    """
    account = await lock_user_account(session, user_id)
    stored = next((held for held in account.roles if held.role is role), None)
    if stored is None:
        raise UserRoleNotFoundError(f"User account {user_id} does not hold the role {role.value}")

    account.roles.remove(stored)
    await session.flush()
    await write_user_account_audit_log(
        session, user_id, AuditEventType.USER_ROLE_REMOVED, f"Removed role {role.value} by {actor}."
    )


async def _require_email_unused(session: AsyncSession, email: str) -> None:
    """The friendly check: it answers before anything is written, but it cannot be the last word."""
    if await session.scalar(select(UserAccount.id).where(UserAccount.email == email)) is not None:
        raise UserEmailAlreadyInUseError("The e-mail address is already in use")


@asynccontextmanager
async def _unique_account(session: AsyncSession) -> AsyncIterator[None]:
    """Write the change made inside the block in a SAVEPOINT and translate a uniqueness conflict.

    The checks above are check-then-insert, so two overlapping requests both pass them; the
    constraint is what decides, and the flush forces the violation here instead of at commit. The
    change must happen *inside* the block: ``begin_nested()`` first flushes whatever is pending
    into the enclosing transaction, where a violation would take the whole transaction down - and
    its message, which names the e-mail address, would be logged.
    """
    try:
        async with session.begin_nested():
            yield
            await session.flush()
    except IntegrityError as exc:
        conflict = _conflict_for(exc)
        if conflict is None:
            raise
        raise conflict from exc


def _conflict_for(exc: IntegrityError) -> UserAccountManagementError | None:
    """The taxonomy error of the violated constraint, or ``None`` for anything else.

    SQLAlchemy's asyncpg dialect wraps the driver error; the raw ``asyncpg`` error, with its
    ``constraint_name``, is its ``__cause__``.
    """
    match getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None):
        case "user_account_party_id_key":
            return UserAccountAlreadyExistsError("The party already has a user account")
        case "user_account_email_key":
            return UserEmailAlreadyInUseError("The e-mail address is already in use")
        case _:
            return None


def _status_event(current: UserAccountStatus) -> AuditEventType:
    """The event of a status that has just changed; the route offers no third value."""
    if current is UserAccountStatus.DISABLED:
        return AuditEventType.USER_ACCOUNT_DISABLED

    return AuditEventType.USER_ACCOUNT_ENABLED


def _stored_roles(account: UserAccount) -> frozenset[Role]:
    return frozenset(Role(stored.role.value) for stored in account.roles)
