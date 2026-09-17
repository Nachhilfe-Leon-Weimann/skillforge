import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.db.dependencies import get_db_session
from app.core.db.models import OperationKind, OperationStatus
from app.services.bot import (
    OperationNotFoundError,
    OperationNotPendingError,
    cancel_operation,
    get_operation,
    list_operations,
)

from .dependencies import BotRead, BotWrite
from .schemas import OperationCancelResponse, OperationPage, OperationResponse, OperationSummary

router = APIRouter(prefix="/operations")


@router.get("", response_model=OperationPage)
async def list_operations_endpoint(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
    guild_id: Annotated[int | None, Query(ge=0)] = None,
    subject_discord_id: Annotated[int | None, Query(ge=0)] = None,
    status_filter: Annotated[OperationStatus | None, Query(alias="status")] = None,
    kind: Annotated[OperationKind | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OperationPage:
    """List operations by subject / status / kind, newest first, for post-restart reconciliation.

    All filters are optional and AND-combined; the subject is the pair
    (`guild_id`, `subject_discord_id`). List items omit the `plan` -- read a single operation by id
    for its full plan.
    """
    operations, total = await list_operations(
        session,
        guild_id=guild_id,
        subject_discord_id=subject_discord_id,
        status=status_filter,
        kind=kind,
        limit=limit,
        offset=offset,
    )
    return OperationPage(
        items=[OperationSummary.from_model(operation) for operation in operations],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{operation_id}",
    response_model=OperationResponse,
    responses=error_responses(OperationNotFoundError),
)
async def read_operation(
    operation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> OperationResponse:
    """Read a single operation by id, including its two-phase `plan`."""
    operation = await get_operation(session, operation_id=operation_id)
    return OperationResponse.from_model(operation)


@router.post(
    "/{operation_id}/cancel",
    response_model=OperationCancelResponse,
    responses=error_responses(OperationNotFoundError, OperationNotPendingError),
)
async def cancel_operation_endpoint(
    operation_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> OperationCancelResponse:
    """Explicitly cancel a PREPARED operation, releasing its reservation immediately.

    Idempotent on an already-cancelled operation; a non-cancellable state (committed, expired,
    failed) returns 409 and an unknown id returns 404.
    """
    operation = await cancel_operation(session, operation_id=operation_id)
    return OperationCancelResponse.from_model(operation)
