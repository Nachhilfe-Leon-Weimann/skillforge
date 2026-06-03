from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
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

COMMAND_ENV_NOT_FOUND = "Command env channel not found"
COMMAND_ENV_INVALID = "Command env references an unknown guild, channel, or owner"
COMMAND_ENV_CONFLICT = "Owner already owns a command env of this kind in the guild"


@router.put(
    "",
    response_model=CommandEnvChannelResponse,
    responses={
        409: error_response(COMMAND_ENV_CONFLICT),
        422: error_response(COMMAND_ENV_INVALID),
    },
)
async def upsert_command_env_endpoint(
    request: CommandEnvUpsertRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> CommandEnvChannelResponse:
    try:
        command_env = await upsert_command_env(
            session,
            guild_id=request.guild_id,
            channel_id=request.channel_id,
            kind=request.kind,
            owner_discord_id=request.owner_discord_id,
        )
    except CommandEnvValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=COMMAND_ENV_INVALID) from exc
    except CommandEnvConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=COMMAND_ENV_CONFLICT) from exc

    return CommandEnvChannelResponse.from_model(command_env)


@router.delete(
    "/{guild_id}/{channel_id}/{kind}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: error_response(COMMAND_ENV_NOT_FOUND)},
)
async def delete_command_env_endpoint(
    guild_id: Annotated[int, Path(ge=0)],
    channel_id: Annotated[int, Path(ge=0)],
    kind: CommandEnvKind,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> None:
    try:
        await delete_command_env(session, guild_id=guild_id, channel_id=channel_id, kind=kind)
    except CommandEnvNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=COMMAND_ENV_NOT_FOUND) from exc
