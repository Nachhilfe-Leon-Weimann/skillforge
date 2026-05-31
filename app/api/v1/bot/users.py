from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
from app.core.db.dependencies import get_db_session
from app.core.db.models import DiscordUser

from .schemas import DiscordUserResponse

router = APIRouter(prefix="/users")
DISCORD_USER_NOT_FOUND = "Discord user not found"


@router.get("", response_model=list[DiscordUserResponse])
async def read_discord_users(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[DiscordUserResponse]:
    result = await session.execute(select(DiscordUser).order_by(DiscordUser.discord_id))
    users = result.scalars().all()
    return [DiscordUserResponse.from_model(user) for user in users]


@router.get(
    "/{discord_id}",
    response_model=DiscordUserResponse,
    responses={
        404: error_response(DISCORD_USER_NOT_FOUND),
    },
)
async def read_discord_user(
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DiscordUserResponse:
    user = await session.get(DiscordUser, discord_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DISCORD_USER_NOT_FOUND,
        )

    return DiscordUserResponse.from_model(user)
