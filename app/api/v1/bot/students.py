import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.dependencies import get_db_session
from app.services.bot import (
    BotServiceError,
    commit_student_activation,
    commit_student_deactivation,
    commit_student_pop,
    commit_student_stash,
    prepare_student_activation,
    prepare_student_deactivation,
    prepare_student_pop,
    prepare_student_stash,
)

from ._transitions import COMMIT_RESPONSES, PREPARE_RESPONSES, transition_http_exception
from .dependencies import BotWrite
from .schemas import (
    StudentActivationCommitRequest,
    StudentActivationPrepareRequest,
    TransitionCommitResponse,
    TransitionPrepareResponse,
)

router = APIRouter(prefix="/students")

GuildId = Annotated[int, Path(ge=0)]
StudentDiscordId = Annotated[int, Path(ge=0)]


# --- activation (static paths declared before the dynamic ones) -------------


@router.post("/activations/prepare", response_model=TransitionPrepareResponse, responses=PREPARE_RESPONSES)
async def prepare_student_activation_endpoint(
    request: StudentActivationPrepareRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionPrepareResponse:
    try:
        operation = await prepare_student_activation(
            session,
            guild_id=request.guild_id,
            student_discord_id=request.student_discord_id,
            tutor_discord_id=request.tutor_discord_id,
        )
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionPrepareResponse.from_model(operation)


@router.post("/activations/{operation_id}/commit", response_model=TransitionCommitResponse, responses=COMMIT_RESPONSES)
async def commit_student_activation_endpoint(
    operation_id: uuid.UUID,
    request: StudentActivationCommitRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionCommitResponse:
    try:
        operation = await commit_student_activation(session, operation_id=operation_id, channel_id=request.channel_id)
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionCommitResponse.from_model(operation)


# --- stash ------------------------------------------------------------------


@router.post(
    "/{guild_id}/{student_discord_id}/stash/prepare",
    response_model=TransitionPrepareResponse,
    responses=PREPARE_RESPONSES,
)
async def prepare_student_stash_endpoint(
    guild_id: GuildId,
    student_discord_id: StudentDiscordId,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionPrepareResponse:
    try:
        operation = await prepare_student_stash(session, guild_id=guild_id, student_discord_id=student_discord_id)
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionPrepareResponse.from_model(operation)


@router.post(
    "/{guild_id}/{student_discord_id}/stash/{operation_id}/commit",
    response_model=TransitionCommitResponse,
    responses=COMMIT_RESPONSES,
)
async def commit_student_stash_endpoint(
    guild_id: GuildId,
    student_discord_id: StudentDiscordId,
    operation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionCommitResponse:
    try:
        operation = await commit_student_stash(session, operation_id=operation_id)
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionCommitResponse.from_model(operation)


# --- pop --------------------------------------------------------------------


@router.post(
    "/{guild_id}/{student_discord_id}/pop/prepare",
    response_model=TransitionPrepareResponse,
    responses=PREPARE_RESPONSES,
)
async def prepare_student_pop_endpoint(
    guild_id: GuildId,
    student_discord_id: StudentDiscordId,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionPrepareResponse:
    try:
        operation = await prepare_student_pop(session, guild_id=guild_id, student_discord_id=student_discord_id)
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionPrepareResponse.from_model(operation)


@router.post(
    "/{guild_id}/{student_discord_id}/pop/{operation_id}/commit",
    response_model=TransitionCommitResponse,
    responses=COMMIT_RESPONSES,
)
async def commit_student_pop_endpoint(
    guild_id: GuildId,
    student_discord_id: StudentDiscordId,
    operation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionCommitResponse:
    try:
        operation = await commit_student_pop(session, operation_id=operation_id)
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionCommitResponse.from_model(operation)


# --- deactivate -------------------------------------------------------------


@router.post(
    "/{guild_id}/{student_discord_id}/deactivate/prepare",
    response_model=TransitionPrepareResponse,
    responses=PREPARE_RESPONSES,
)
async def prepare_student_deactivation_endpoint(
    guild_id: GuildId,
    student_discord_id: StudentDiscordId,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionPrepareResponse:
    try:
        operation = await prepare_student_deactivation(
            session, guild_id=guild_id, student_discord_id=student_discord_id
        )
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionPrepareResponse.from_model(operation)


@router.post(
    "/{guild_id}/{student_discord_id}/deactivate/{operation_id}/commit",
    response_model=TransitionCommitResponse,
    responses=COMMIT_RESPONSES,
)
async def commit_student_deactivation_endpoint(
    guild_id: GuildId,
    student_discord_id: StudentDiscordId,
    operation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> TransitionCommitResponse:
    try:
        operation = await commit_student_deactivation(session, operation_id=operation_id)
    except BotServiceError as exc:
        raise transition_http_exception(exc) from exc

    return TransitionCommitResponse.from_model(operation)
