from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
from app.core.db.dependencies import get_db_session
from app.services.bot import (
    AccountLinkConflictError,
    DiscordAccountNotFoundError,
    GroupMembershipNotFoundError,
    PartyNotFoundError,
    PermissionGroupNotFoundError,
    PrincipalNotFoundError,
    add_user_to_group,
    deactivate_discord_account,
    link_discord_account,
    remove_user_from_group,
    upsert_discord_user,
)

from .dependencies import BotWrite
from .schemas import (
    DiscordAccountLinkRequest,
    DiscordAccountLinkResponse,
    DiscordUserResponse,
    DiscordUserUpsertRequest,
    GroupMembershipResponse,
)

router = APIRouter(prefix="/users")

PARTY_NOT_FOUND = "Party not found"
ACCOUNT_LINK_CONFLICT = "Another primary account already exists for this party"
ACCOUNT_NOT_FOUND = "Discord account not found"
USER_NOT_FOUND = "Discord user not found"
GROUP_NOT_FOUND = "Permission group not found"
GROUP_MEMBERSHIP_NOT_FOUND = "Group membership not found"

DiscordId = Annotated[int, Path(ge=0)]
GroupKey = Annotated[str, Path(min_length=1)]


@router.put("/{discord_id}", response_model=DiscordUserResponse)
async def upsert_discord_user_endpoint(
    discord_id: DiscordId,
    request: DiscordUserUpsertRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> DiscordUserResponse:
    """Register or update a Discord user.

    Idempotent: re-registering an existing `discord_id` updates role/nick/active instead of
    conflicting, so the bot can safely replay an onboarding event.
    """
    user = await upsert_discord_user(
        session,
        discord_id=discord_id,
        role=request.role,
        nick_name=request.nick_name,
        active=request.active,
    )
    return DiscordUserResponse.from_model(user)


@router.put(
    "/{discord_id}/account",
    response_model=DiscordAccountLinkResponse,
    responses={
        404: error_response(PARTY_NOT_FOUND),
        409: error_response(ACCOUNT_LINK_CONFLICT),
    },
)
async def link_discord_account_endpoint(
    discord_id: DiscordId,
    request: DiscordAccountLinkRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> DiscordAccountLinkResponse:
    """Link a Discord account to an existing party.

    The party must already exist (the CRM owns party creation); linking to an unknown party returns
    404. Promoting an account to primary demotes the party's current primary account in the same
    transaction.
    """
    try:
        account = await link_discord_account(
            session,
            discord_id=discord_id,
            party_id=request.party_id,
            is_primary=request.is_primary,
            active=request.active,
        )
    except PartyNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PARTY_NOT_FOUND) from exc
    except AccountLinkConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=ACCOUNT_LINK_CONFLICT) from exc

    return DiscordAccountLinkResponse.from_model(account)


@router.delete(
    "/{discord_id}/account",
    response_model=DiscordAccountLinkResponse,
    responses={404: error_response(ACCOUNT_NOT_FOUND)},
)
async def deactivate_discord_account_endpoint(
    discord_id: DiscordId,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> DiscordAccountLinkResponse:
    """Deactivate (unlink) a Discord account without deleting it.

    Sets `active` to false and clears `is_primary`, freeing the party's primary slot while keeping
    the row for history. Re-linking via the `PUT` endpoint reactivates it.
    """
    try:
        account = await deactivate_discord_account(session, discord_id=discord_id)
    except DiscordAccountNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ACCOUNT_NOT_FOUND) from exc

    return DiscordAccountLinkResponse.from_model(account)


@router.put(
    "/{discord_id}/groups/{group_key}",
    response_model=GroupMembershipResponse,
    responses={404: error_response(f"{USER_NOT_FOUND} or {GROUP_NOT_FOUND}")},
)
async def add_user_to_group_endpoint(
    discord_id: DiscordId,
    group_key: GroupKey,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> GroupMembershipResponse:
    """Add a Discord user to a permission group. Idempotent on (user, group)."""
    try:
        membership = await add_user_to_group(session, discord_id=discord_id, group_key=group_key)
    except PrincipalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=USER_NOT_FOUND) from exc
    except PermissionGroupNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GROUP_NOT_FOUND) from exc

    return GroupMembershipResponse.from_model(membership)


@router.delete(
    "/{discord_id}/groups/{group_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: error_response(GROUP_MEMBERSHIP_NOT_FOUND)},
)
async def remove_user_from_group_endpoint(
    discord_id: DiscordId,
    group_key: GroupKey,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> None:
    """Remove a Discord user from a permission group."""
    try:
        await remove_user_from_group(session, discord_id=discord_id, group_key=group_key)
    except GroupMembershipNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=GROUP_MEMBERSHIP_NOT_FOUND) from exc
