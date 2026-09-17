from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.db.dependencies import get_db_session
from app.core.db.models import CommandEnvKind
from app.services.bot import (
    CommandEnvNotFoundError,
    PrincipalNotFoundError,
    PrincipalView,
    StudentContextNotFoundError,
    TutorContextNotFoundError,
    get_principal_view,
    get_principal_views,
    get_student_context_view,
    get_student_context_views,
    get_tutor_context_view,
    get_tutor_context_views,
    resolve_command_env,
)

from .dependencies import BotRead
from .schemas import (
    BotPrincipal,
    CommandEnvChannelResponse,
    DiscordIdBatchRequest,
    PrincipalBatch,
    StudentContext,
    StudentContextBatch,
    TutorContext,
    TutorContextBatch,
)

router = APIRouter(prefix="/runtime")


def _to_principal(view: PrincipalView) -> BotPrincipal:
    return BotPrincipal.from_parts(
        user=view.user,
        group_keys=view.group_keys,
        permission_keys=view.permission_keys,
        party=view.party,
    )


def _split_found_missing[T, R](
    requested: list[int],
    by_id: dict[int, T],
    to_response: Callable[[T], R],
) -> tuple[list[R], list[int]]:
    ordered_ids = list(dict.fromkeys(requested))
    found = [to_response(by_id[discord_id]) for discord_id in ordered_ids if discord_id in by_id]
    missing = [discord_id for discord_id in ordered_ids if discord_id not in by_id]
    return found, missing


@router.get(
    "/principals/{discord_id}",
    response_model=BotPrincipal,
    responses=error_responses(PrincipalNotFoundError),
)
async def read_principal(
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> BotPrincipal:
    view = await get_principal_view(session, discord_id)
    return _to_principal(view)


@router.get(
    "/tutors/{guild_id}/{discord_id}",
    response_model=TutorContext,
    responses=error_responses(TutorContextNotFoundError),
)
async def read_tutor_context(
    guild_id: Annotated[int, Path(ge=0)],
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> TutorContext:
    view = await get_tutor_context_view(session, guild_id=guild_id, tutor_discord_id=discord_id)
    return TutorContext.from_parts(principal=_to_principal(view.principal), workspace=view.workspace)


@router.get(
    "/students/{guild_id}/{discord_id}",
    response_model=StudentContext,
    responses=error_responses(StudentContextNotFoundError),
)
async def read_student_context(
    guild_id: Annotated[int, Path(ge=0)],
    discord_id: Annotated[int, Path(ge=0)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> StudentContext:
    view = await get_student_context_view(session, guild_id=guild_id, student_discord_id=discord_id)
    return StudentContext.from_parts(
        principal=_to_principal(view.principal),
        workspace=view.workspace,
        party_id=view.party_id,
    )


@router.post("/principals/batch", response_model=PrincipalBatch)
async def read_principals_batch(
    request: DiscordIdBatchRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> PrincipalBatch:
    """Resolve many principals in one request. Unresolved ids are returned in `missing`, never 404."""
    views = await get_principal_views(session, request.discord_ids)
    by_id = {view.user.discord_id: view for view in views}
    found, missing = _split_found_missing(request.discord_ids, by_id, _to_principal)
    return PrincipalBatch(found=found, missing=missing)


@router.post("/tutors/{guild_id}/batch", response_model=TutorContextBatch)
async def read_tutor_contexts_batch(
    guild_id: Annotated[int, Path(ge=0)],
    request: DiscordIdBatchRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> TutorContextBatch:
    """Resolve many tutor contexts within one guild. Ids without a workspace are returned in `missing`."""
    views = await get_tutor_context_views(session, guild_id=guild_id, tutor_discord_ids=request.discord_ids)
    by_id = {view.workspace.tutor_discord_id: view for view in views}
    found, missing = _split_found_missing(
        request.discord_ids,
        by_id,
        lambda view: TutorContext.from_parts(principal=_to_principal(view.principal), workspace=view.workspace),
    )
    return TutorContextBatch(found=found, missing=missing)


@router.post("/students/{guild_id}/batch", response_model=StudentContextBatch)
async def read_student_contexts_batch(
    guild_id: Annotated[int, Path(ge=0)],
    request: DiscordIdBatchRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> StudentContextBatch:
    """Resolve many student contexts within one guild. Ids without a workspace are returned in `missing`."""
    views = await get_student_context_views(session, guild_id=guild_id, student_discord_ids=request.discord_ids)
    by_id = {view.workspace.student_discord_id: view for view in views}
    found, missing = _split_found_missing(
        request.discord_ids,
        by_id,
        lambda view: StudentContext.from_parts(
            principal=_to_principal(view.principal), workspace=view.workspace, party_id=view.party_id
        ),
    )
    return StudentContextBatch(found=found, missing=missing)


@router.get(
    "/command-envs/resolve",
    response_model=CommandEnvChannelResponse,
    responses=error_responses(CommandEnvNotFoundError),
)
async def resolve_command_env_endpoint(
    guild_id: Annotated[int, Query(ge=0)],
    channel_id: Annotated[int, Query(ge=0)],
    kind: Annotated[CommandEnvKind, Query()],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
    owner_discord_id: Annotated[int | None, Query(ge=0)] = None,
) -> CommandEnvChannelResponse:
    command_env = await resolve_command_env(
        session,
        guild_id=guild_id,
        channel_id=channel_id,
        kind=kind,
        owner_discord_id=owner_discord_id,
    )
    return CommandEnvChannelResponse.from_model(command_env)
