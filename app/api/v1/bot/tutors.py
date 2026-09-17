import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.db.dependencies import get_db_session
from app.services.bot import (
    OperationNotFoundError,
    OperationNotPendingError,
    TransitionConflictError,
    TransitionValidationError,
    commit_tutor_activation,
    commit_tutor_deactivation,
    prepare_tutor_activation,
    prepare_tutor_deactivation,
)

from .dependencies import BotWrite
from .schemas import (
    TransitionCommitResponse,
    TransitionPrepareResponse,
    TutorActivationCommitRequest,
    TutorActivationPrepareRequest,
)

router = APIRouter(prefix="/tutors")

GuildId = Annotated[int, Path(ge=0)]
TutorDiscordId = Annotated[int, Path(ge=0)]


@router.post(
    "/activations/prepare",
    response_model=TransitionPrepareResponse,
    responses=error_responses(TransitionConflictError, TransitionValidationError),
)
async def prepare_tutor_activation_endpoint(
    request: TutorActivationPrepareRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionPrepareResponse:
    operation = await prepare_tutor_activation(
        session, guild_id=request.guild_id, tutor_discord_id=request.tutor_discord_id
    )
    return TransitionPrepareResponse.from_model(operation)


@router.post(
    "/activations/{operation_id}/commit",
    response_model=TransitionCommitResponse,
    responses=error_responses(OperationNotFoundError, OperationNotPendingError, TransitionConflictError),
)
async def commit_tutor_activation_endpoint(
    operation_id: uuid.UUID,
    request: TutorActivationCommitRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionCommitResponse:
    operation = await commit_tutor_activation(
        session,
        operation_id=operation_id,
        category_channel_id=request.category_channel_id,
        command_channel_id=request.command_channel_id,
    )
    return TransitionCommitResponse.from_model(operation)


# --- deactivate -------------------------------------------------------------


@router.post(
    "/{guild_id}/{tutor_discord_id}/deactivate/prepare",
    response_model=TransitionPrepareResponse,
    responses=error_responses(TransitionConflictError, TransitionValidationError),
)
async def prepare_tutor_deactivation_endpoint(
    guild_id: GuildId,
    tutor_discord_id: TutorDiscordId,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionPrepareResponse:
    operation = await prepare_tutor_deactivation(session, guild_id=guild_id, tutor_discord_id=tutor_discord_id)
    return TransitionPrepareResponse.from_model(operation)


@router.post(
    "/{guild_id}/{tutor_discord_id}/deactivate/{operation_id}/commit",
    response_model=TransitionCommitResponse,
    responses=error_responses(OperationNotFoundError, OperationNotPendingError, TransitionConflictError),
)
async def commit_tutor_deactivation_endpoint(
    guild_id: GuildId,
    tutor_discord_id: TutorDiscordId,
    operation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionCommitResponse:
    operation = await commit_tutor_deactivation(session, operation_id=operation_id)
    return TransitionCommitResponse.from_model(operation)
