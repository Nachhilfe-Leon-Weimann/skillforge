import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import Page, PageParams, error_responses
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
from .schemas import OperationCancelResponse, OperationResponse, OperationSummary

router = APIRouter(prefix="/operations")


class OperationListParams(PageParams):
    guild_id: int | None = Field(None, ge=0, description="Only operations in this guild.")
    subject_discord_id: int | None = Field(None, ge=0, description="Only operations on this subject (Discord user).")
    status: OperationStatus | None = Field(None, description="Only operations in this status.")
    kind: OperationKind | None = Field(None, description="Only operations of this kind.")


type OperationListQuery = Annotated[OperationListParams, Query()]


@router.get("")
async def list_operations_endpoint(
    params: OperationListQuery,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> Page[OperationSummary]:
    """List operations by subject / status / kind, newest first, for post-restart reconciliation.

    All filters are optional and AND-combined; the subject is the pair
    (`guild_id`, `subject_discord_id`). List items omit the `plan` -- read a single operation by id
    for its full plan.
    """
    operations, total = await list_operations(session, **params.model_dump())
    return Page.of([OperationSummary.from_model(operation) for operation in operations], total=total, params=params)


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
