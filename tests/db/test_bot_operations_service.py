import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.db.models import Operation, OperationKind, OperationStatus
from app.services.bot import (
    OperationNotFoundError,
    get_operation,
    list_operations,
)

# Postgres ``now()`` is the transaction timestamp, so rows inserted in one test transaction all share
# a ``created_at``. Tests that assert ordering/pagination therefore set ``created_at`` explicitly.
# Bulk seeding varies ``subject_discord_id``: the partial unique index
# ``uq_operation_prepared_subject_kind`` allows only one open PREPARED row per (guild, subject, kind).


def _operation(
    *,
    subject_discord_id: int,
    kind: OperationKind = OperationKind.STUDENT_STASH,
    guild_id: int = 1,
    status: OperationStatus = OperationStatus.PREPARED,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    plan: dict | None = None,
    operation_id: uuid.UUID | None = None,
) -> Operation:
    operation = Operation(
        kind=kind,
        guild_id=guild_id,
        subject_discord_id=subject_discord_id,
        status=status,
        expires_at=expires_at or (datetime.now(UTC) + timedelta(minutes=10)),
        plan=plan if plan is not None else {},
    )
    if created_at is not None:
        operation.created_at = created_at
    if operation_id is not None:
        operation.operation_id = operation_id
    return operation


async def _add(session, operation: Operation) -> Operation:
    session.add(operation)
    await session.flush()
    return operation


@pytest.mark.db
async def test_get_operation_returns_operation(session):
    op = await _add(session, _operation(subject_discord_id=100, plan={"step": "reserve"}))

    fetched = await get_operation(session, operation_id=op.operation_id)

    assert fetched.operation_id == op.operation_id
    assert fetched.subject_discord_id == 100
    assert fetched.plan == {"step": "reserve"}


@pytest.mark.db
async def test_get_operation_unknown_raises(session):
    with pytest.raises(OperationNotFoundError):
        await get_operation(session, operation_id=uuid.uuid4())


@pytest.mark.db
async def test_list_operations_filters_by_status(session):
    await _add(session, _operation(subject_discord_id=1, status=OperationStatus.PREPARED))
    await _add(session, _operation(subject_discord_id=2, status=OperationStatus.COMMITTED))

    items, total = await list_operations(session, status=OperationStatus.COMMITTED)

    assert total == 1
    assert [o.subject_discord_id for o in items] == [2]


@pytest.mark.db
async def test_list_operations_filters_by_kind(session):
    await _add(session, _operation(subject_discord_id=1, kind=OperationKind.STUDENT_STASH))
    await _add(session, _operation(subject_discord_id=2, kind=OperationKind.TUTOR_ACTIVATE))

    items, total = await list_operations(session, kind=OperationKind.TUTOR_ACTIVATE)

    assert total == 1
    assert items[0].kind is OperationKind.TUTOR_ACTIVATE


@pytest.mark.db
async def test_list_operations_filters_by_subject(session):
    await _add(session, _operation(subject_discord_id=100, guild_id=1))
    await _add(session, _operation(subject_discord_id=200, guild_id=1))
    await _add(session, _operation(subject_discord_id=100, guild_id=2))

    items, total = await list_operations(session, guild_id=1, subject_discord_id=100)

    assert total == 1
    assert items[0].subject_discord_id == 100
    assert items[0].guild_id == 1


@pytest.mark.db
async def test_list_operations_and_combines_filters(session):
    await _add(
        session,
        _operation(subject_discord_id=1, status=OperationStatus.COMMITTED, kind=OperationKind.STUDENT_STASH),
    )
    await _add(
        session,
        _operation(subject_discord_id=2, status=OperationStatus.COMMITTED, kind=OperationKind.TUTOR_ACTIVATE),
    )
    await _add(
        session,
        _operation(subject_discord_id=3, status=OperationStatus.PREPARED, kind=OperationKind.STUDENT_STASH),
    )

    items, total = await list_operations(session, status=OperationStatus.COMMITTED, kind=OperationKind.STUDENT_STASH)

    assert total == 1
    assert items[0].subject_discord_id == 1


@pytest.mark.db
async def test_list_operations_paginates_with_total(session):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        await _add(
            session,
            _operation(
                subject_discord_id=i,
                status=OperationStatus.COMMITTED,
                created_at=base + timedelta(minutes=i),
            ),
        )

    page1, total = await list_operations(session, status=OperationStatus.COMMITTED, limit=2, offset=0)
    page2, total2 = await list_operations(session, status=OperationStatus.COMMITTED, limit=2, offset=2)

    assert total == 5
    assert total2 == 5
    # Newest first (created_at desc): subject 4 has the latest created_at.
    assert [o.subject_discord_id for o in page1] == [4, 3]
    assert [o.subject_discord_id for o in page2] == [2, 1]


@pytest.mark.db
async def test_list_operations_orders_newest_first(session):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await _add(session, _operation(subject_discord_id=1, status=OperationStatus.COMMITTED, created_at=base))
    await _add(
        session,
        _operation(subject_discord_id=2, status=OperationStatus.COMMITTED, created_at=base + timedelta(hours=1)),
    )

    items, _ = await list_operations(session, status=OperationStatus.COMMITTED)

    assert [o.subject_discord_id for o in items] == [2, 1]


@pytest.mark.db
async def test_list_operations_without_filters_returns_all(session):
    await _add(session, _operation(subject_discord_id=1))
    await _add(session, _operation(subject_discord_id=2))

    items, total = await list_operations(session)

    assert total == 2
    assert len(items) == 2


@pytest.mark.db
async def test_list_operations_tiebreaks_equal_created_at_by_id(session):
    # Rows written in one transaction share Postgres now(); order must still be deterministic via the
    # operation_id.desc() tiebreaker (production path: several operations prepared in one transaction).
    same = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [
        uuid.UUID("00000000-0000-0000-0000-000000000001"),
        uuid.UUID("00000000-0000-0000-0000-000000000002"),
        uuid.UUID("00000000-0000-0000-0000-000000000003"),
    ]
    for index, operation_id in enumerate(ids):
        await _add(
            session,
            _operation(
                subject_discord_id=index,
                status=OperationStatus.COMMITTED,
                created_at=same,
                operation_id=operation_id,
            ),
        )

    # Page one row at a time; the union must be the full set with no duplicates or gaps.
    seen = []
    for offset in range(3):
        page, total = await list_operations(session, status=OperationStatus.COMMITTED, limit=1, offset=offset)
        assert total == 3
        assert len(page) == 1
        seen.append(page[0].operation_id)

    assert seen == list(reversed(ids))  # deterministic operation_id.desc()
    assert len(set(seen)) == 3


@pytest.mark.db
async def test_list_operations_offset_beyond_total_is_empty(session):
    await _add(session, _operation(subject_discord_id=1, status=OperationStatus.COMMITTED))
    await _add(session, _operation(subject_discord_id=2, status=OperationStatus.COMMITTED))

    items, total = await list_operations(session, status=OperationStatus.COMMITTED, limit=10, offset=100)

    assert total == 2
    assert items == []
