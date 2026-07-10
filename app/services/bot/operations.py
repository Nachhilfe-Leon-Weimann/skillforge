"""Read plane for two-phase operations.

Query helpers that let the bot reconcile after a restart -- fetch a single operation by id (with its
``plan``) or list operations filtered by subject / status / kind. Writes (prepare/commit) live in
:mod:`app.services.bot.transitions`; this module is read-only.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Operation, OperationKind, OperationStatus

from .errors import OperationNotFoundError


async def get_operation(session: AsyncSession, *, operation_id: uuid.UUID) -> Operation:
    """Return a single operation by id, including its ``plan``.

    Raises :class:`OperationNotFoundError` if no operation has that id.
    """
    operation = await session.get(Operation, operation_id)
    if operation is None:
        raise OperationNotFoundError(f"No operation with id {operation_id}")
    return operation


async def list_operations(
    session: AsyncSession,
    *,
    guild_id: int | None = None,
    subject_discord_id: int | None = None,
    status: OperationStatus | None = None,
    kind: OperationKind | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Operation], int]:
    """List operations matching the given filters, newest first, with the total match count.

    All filters are optional and AND-combined. The subject of an operation is the pair
    ``(guild_id, subject_discord_id)``; each is an independent filter. Returns ``(page, total)``
    where ``page`` is at most ``limit`` operations starting at ``offset`` and ``total`` is the number
    of operations matching the filters regardless of pagination.
    """
    filters = []
    if guild_id is not None:
        filters.append(Operation.guild_id == guild_id)
    if subject_discord_id is not None:
        filters.append(Operation.subject_discord_id == subject_discord_id)
    if status is not None:
        filters.append(Operation.status == status)
    if kind is not None:
        filters.append(Operation.kind == kind)

    total = (await session.execute(select(func.count()).select_from(Operation).where(*filters))).scalar_one()

    statement = (
        select(Operation)
        .where(*filters)
        .order_by(Operation.created_at.desc(), Operation.operation_id.desc())
        .limit(limit)
        .offset(offset)
    )
    operations = (await session.execute(statement)).scalars().all()
    return operations, total
