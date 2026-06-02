from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
from app.core.db.dependencies import get_db_session
from app.core.db.models import CommandEnvKind
from app.services.bot import (
    CommandEnvNotFoundError,
    PrincipalNotFoundError,
    PrincipalView,
    StudentContextNotFoundError,
    TutorContextNotFoundError,
    get_principal_view,
    get_student_context_view,
    get_tutor_context_view,
    resolve_command_env,
)

from .dependencies import BotRead
from .schemas import BotPrincipal, CommandEnvChannelResponse, StudentContext, TutorContext

router = APIRouter(prefix="/runtime")

PRINCIPAL_NOT_FOUND = "Discord principal not found"
TUTOR_CONTEXT_NOT_FOUND = "Tutor context not found"
STUDENT_CONTEXT_NOT_FOUND = "Student context not found"
COMMAND_ENV_NOT_FOUND = "Command env channel not found"


def _to_principal(view: PrincipalView) -> BotPrincipal:
    return BotPrincipal.from_parts(
        user=view.user,
        group_keys=view.group_keys,
        permission_keys=view.permission_keys,
        party=view.party,
    )


@router.get(
    "/principals/{discord_id}",
    response_model=BotPrincipal,
    responses={404: error_response(PRINCIPAL_NOT_FOUND)},
)
async def read_principal(
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> BotPrincipal:
    try:
        view = await get_principal_view(session, discord_id)
    except PrincipalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PRINCIPAL_NOT_FOUND) from exc

    return _to_principal(view)


@router.get(
    "/tutors/{guild_id}/{discord_id}",
    response_model=TutorContext,
    responses={404: error_response(TUTOR_CONTEXT_NOT_FOUND)},
)
async def read_tutor_context(
    guild_id: Annotated[int, Path(ge=0)],
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> TutorContext:
    try:
        view = await get_tutor_context_view(session, guild_id=guild_id, tutor_discord_id=discord_id)
    except TutorContextNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TUTOR_CONTEXT_NOT_FOUND) from exc

    return TutorContext.from_parts(principal=_to_principal(view.principal), workspace=view.workspace)


@router.get(
    "/students/{guild_id}/{discord_id}",
    response_model=StudentContext,
    responses={404: error_response(STUDENT_CONTEXT_NOT_FOUND)},
)
async def read_student_context(
    guild_id: Annotated[int, Path(ge=0)],
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> StudentContext:
    try:
        view = await get_student_context_view(session, guild_id=guild_id, student_discord_id=discord_id)
    except StudentContextNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=STUDENT_CONTEXT_NOT_FOUND) from exc

    return StudentContext.from_parts(
        principal=_to_principal(view.principal),
        workspace=view.workspace,
        party_id=view.party_id,
    )


@router.get(
    "/command-envs/resolve",
    response_model=CommandEnvChannelResponse,
    responses={404: error_response(COMMAND_ENV_NOT_FOUND)},
)
async def resolve_command_env_endpoint(
    guild_id: Annotated[int, Query(ge=0)],
    channel_id: Annotated[int, Query(ge=0)],
    kind: Annotated[CommandEnvKind, Query()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
    owner_discord_id: Annotated[int | None, Query(ge=0)] = None,
) -> CommandEnvChannelResponse:
    try:
        command_env = await resolve_command_env(
            session,
            guild_id=guild_id,
            channel_id=channel_id,
            kind=kind,
            owner_discord_id=owner_discord_id,
        )
    except CommandEnvNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=COMMAND_ENV_NOT_FOUND) from exc

    return CommandEnvChannelResponse.from_model(command_env)
