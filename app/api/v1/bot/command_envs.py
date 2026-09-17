from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.db.dependencies import get_db_session
from app.core.db.models import CommandEnvKind
from app.services.bot import (
    CommandEnvConflictError,
    CommandEnvNotFoundError,
    CommandEnvValidationError,
    delete_command_env,
    upsert_command_env,
)

from .dependencies import BotWrite
from .schemas import CommandEnvChannelResponse, CommandEnvUpsertRequest

router = APIRouter(prefix="/command-envs")


@router.put(
    "",
    response_model=CommandEnvChannelResponse,
    responses=error_responses(CommandEnvConflictError, CommandEnvValidationError),
)
async def upsert_command_env_endpoint(
    request: CommandEnvUpsertRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> CommandEnvChannelResponse:
    command_env = await upsert_command_env(
        session,
        guild_id=request.guild_id,
        channel_id=request.channel_id,
        kind=request.kind,
        owner_discord_id=request.owner_discord_id,
    )
    return CommandEnvChannelResponse.from_model(command_env)


@router.delete(
    "/{guild_id}/{channel_id}/{kind}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(CommandEnvNotFoundError),
)
async def delete_command_env_endpoint(
    guild_id: Annotated[int, Path(ge=0)],
    channel_id: Annotated[int, Path(ge=0)],
    kind: CommandEnvKind,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> None:
    await delete_command_env(session, guild_id=guild_id, channel_id=channel_id, kind=kind)
