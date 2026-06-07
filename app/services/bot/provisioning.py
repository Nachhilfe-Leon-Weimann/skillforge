import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import (
    DiscordAccount,
    DiscordUser,
    DiscordUserPermissionGroup,
    MemberRole,
    Party,
    PermissionGroup,
)

from .errors import (
    AccountLinkConflictError,
    DiscordAccountNotFoundError,
    GroupMembershipNotFoundError,
    PartyNotFoundError,
    PermissionGroupNotFoundError,
    PrincipalNotFoundError,
)


async def upsert_discord_user(
    session: AsyncSession,
    *,
    discord_id: int,
    role: MemberRole,
    nick_name: str,
    active: bool = True,
) -> DiscordUser:
    """Create or update the Discord user identified by ``discord_id``.

    Idempotent: re-registering an existing user updates its mutable fields instead of conflicting, so
    the bot can safely replay an onboarding event after an at-least-once retry.
    """
    user = await session.get(DiscordUser, discord_id)
    if user is None:
        user = DiscordUser(discord_id=discord_id, role=role, nick_name=nick_name, active=active)
        session.add(user)
    else:
        user.role = role
        user.nick_name = nick_name
        user.active = active

    await session.flush()
    return user


async def link_discord_account(
    session: AsyncSession,
    *,
    discord_id: int,
    party_id: uuid.UUID,
    is_primary: bool = False,
    active: bool = True,
) -> DiscordAccount:
    """Link the Discord account ``discord_id`` to an **existing** ``party_id``.

    The CRM owns party creation, so linking to an unknown party raises :class:`PartyNotFoundError`
    (never auto-created here). Idempotent on ``discord_id``: re-linking updates the existing row.
    Promoting an account to primary demotes the party's current primary+active account first, so the
    one-primary-active-per-party partial unique index is never transiently violated.
    """
    if await session.get(Party, party_id) is None:
        raise PartyNotFoundError("Party not found")

    if is_primary and active:
        # Demote the party's current primary+active account (if any) and flush before touching this
        # one, so the two UPDATE/INSERT statements never coexist as two primaries for the party.
        existing_primaries = (
            await session.scalars(
                select(DiscordAccount).where(
                    DiscordAccount.party_id == party_id,
                    DiscordAccount.is_primary.is_(True),
                    DiscordAccount.active.is_(True),
                    DiscordAccount.discord_id != discord_id,
                )
            )
        ).all()
        if existing_primaries:
            for account in existing_primaries:
                account.is_primary = False
            await session.flush()

    account = await session.get(DiscordAccount, discord_id)
    if account is None:
        account = DiscordAccount(discord_id=discord_id, party_id=party_id, is_primary=is_primary, active=active)
        session.add(account)
    else:
        account.party_id = party_id
        account.is_primary = is_primary
        account.active = active

    # Flush inside a SAVEPOINT so a uniqueness violation only rolls back this statement and leaves the
    # surrounding transaction usable (mirrors upsert_command_env). Demotion above makes this defensive.
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise AccountLinkConflictError("Another primary account already exists for this party") from exc

    return account


async def deactivate_discord_account(session: AsyncSession, *, discord_id: int) -> DiscordAccount:
    """Deactivate the Discord account link for ``discord_id`` (unlink without deleting).

    Sets ``active = False`` and clears ``is_primary`` so the party's primary slot is freed, while the
    row is retained (consistent with the "terminal rows are retained" discipline). Re-linking via
    :func:`link_discord_account` reactivates it. Raises :class:`DiscordAccountNotFoundError` if no
    account is linked.
    """
    account = await session.get(DiscordAccount, discord_id)
    if account is None:
        raise DiscordAccountNotFoundError("Discord account not found")

    account.active = False
    account.is_primary = False
    await session.flush()
    return account


async def add_user_to_group(session: AsyncSession, *, discord_id: int, group_key: str) -> DiscordUserPermissionGroup:
    """Add a Discord user to a permission group. Idempotent: an existing membership is returned as-is.

    Validates both the user and the group up front so the common errors surface cleanly
    (:class:`PrincipalNotFoundError` / :class:`PermissionGroupNotFoundError`) instead of as a foreign
    key violation.
    """
    if await session.get(DiscordUser, discord_id) is None:
        raise PrincipalNotFoundError("Discord principal not found")
    if await session.get(PermissionGroup, group_key) is None:
        raise PermissionGroupNotFoundError("Permission group not found")

    membership = await session.get(DiscordUserPermissionGroup, {"discord_id": discord_id, "group_key": group_key})
    if membership is None:
        membership = DiscordUserPermissionGroup(discord_id=discord_id, group_key=group_key)
        session.add(membership)
        await session.flush()

    return membership


async def remove_user_from_group(session: AsyncSession, *, discord_id: int, group_key: str) -> None:
    """Remove a Discord user from a permission group.

    Raises :class:`GroupMembershipNotFoundError` if the user is not a member of the group.
    """
    membership = await session.get(DiscordUserPermissionGroup, {"discord_id": discord_id, "group_key": group_key})
    if membership is None:
        raise GroupMembershipNotFoundError("Group membership not found")

    await session.delete(membership)
    await session.flush()
