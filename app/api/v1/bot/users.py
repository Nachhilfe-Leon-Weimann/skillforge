from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
from app.core.db.dependencies import get_db_session
from app.services.bot import (
    AccountLinkConflictError,
    PartyNotFoundError,
    link_discord_account,
    upsert_discord_user,
)

from .dependencies import BotWrite
from .schemas import (
    DiscordAccountLinkRequest,
    DiscordAccountLinkResponse,
    DiscordUserResponse,
    DiscordUserUpsertRequest,
)

router = APIRouter(prefix="/users")

PARTY_NOT_FOUND = "Party not found"
ACCOUNT_LINK_CONFLICT = "Another primary account already exists for this party"

DiscordId = Annotated[int, Path(ge=0)]


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
