import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from app.core.db.models import Job, JobStatus
from app.services.bot import (
    JobNotClaimedError,
    JobNotFoundError,
    claim_jobs,
    complete_job,
    enqueue_job,
    fail_job,
    get_job,
    get_job_queue_summary,
    list_jobs,
)


@pytest.mark.db
async def test_enqueue_and_claim_marks_claimed(session):
    await enqueue_job(session, kind="activate_tutor", payload={"x": 1})

    claimed = await claim_jobs(session, limit=1, worker="shard-1")

    assert len(claimed) == 1
    job = claimed[0]
    assert job.status is JobStatus.CLAIMED
    assert job.attempt == 1
    assert job.claimed_by == "shard-1"
    assert job.claimed_at is not None


@pytest.mark.db
async def test_claim_respects_limit_and_orders_by_availability(session):
    now = datetime.now(UTC)
    await enqueue_job(session, kind="t", payload={"n": 1}, available_at=now - timedelta(minutes=3))
    await enqueue_job(session, kind="t", payload={"n": 2}, available_at=now - timedelta(minutes=2))
    await enqueue_job(session, kind="t", payload={"n": 3}, available_at=now - timedelta(minutes=1))

    claimed = await claim_jobs(session, limit=2)

    assert [job.payload["n"] for job in claimed] == [1, 2]


@pytest.mark.db
async def test_claim_skips_jobs_not_yet_available(session):
    await enqueue_job(session, kind="t", available_at=datetime.now(UTC) + timedelta(hours=1))

    assert await claim_jobs(session, limit=10) == []


@pytest.mark.db
async def test_claim_filters_by_kind(session):
    await enqueue_job(session, kind="a")
    await enqueue_job(session, kind="b")

    claimed = await claim_jobs(session, kinds=["a"], limit=10)

    assert [job.kind for job in claimed] == ["a"]


@pytest.mark.db
async def test_complete_job_marks_completed(session):
    await enqueue_job(session, kind="t")
    [job] = await claim_jobs(session, limit=1)

    completed = await complete_job(session, job_id=job.job_id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.completed_at is not None


@pytest.mark.db
async def test_complete_unclaimed_job_raises(session):
    job = await enqueue_job(session, kind="t")

    with pytest.raises(JobNotClaimedError):
        await complete_job(session, job_id=job.job_id)


@pytest.mark.db
async def test_complete_unknown_job_raises(session):
    with pytest.raises(JobNotFoundError):
        await complete_job(session, job_id=uuid4())


@pytest.mark.db
async def test_fail_job_terminal(session):
    await enqueue_job(session, kind="t")
    [job] = await claim_jobs(session, limit=1)

    failed = await fail_job(session, job_id=job.job_id, error="boom", retry=False)

    assert failed.status is JobStatus.FAILED
    assert failed.failed_at is not None
    assert failed.last_error == "boom"


@pytest.mark.db
async def test_fail_job_retry_requeues_with_backoff(session):
    await enqueue_job(session, kind="t")
    [job] = await claim_jobs(session, limit=1)

    failed = await fail_job(session, job_id=job.job_id, error="transient", retry=True)

    assert failed.status is JobStatus.PENDING
    assert failed.claimed_at is None
    assert failed.claimed_by is None
    assert failed.available_at > datetime.now(UTC)
    assert failed.attempt == 1


@pytest.mark.db
async def test_fail_job_retry_exhausted_is_terminal(session):
    await enqueue_job(session, kind="t", max_attempts=1)
    [job] = await claim_jobs(session, limit=1)  # attempt becomes 1, == max_attempts

    failed = await fail_job(session, job_id=job.job_id, retry=True)

    assert failed.status is JobStatus.FAILED


# --- read plane: get_job / list_jobs / get_job_queue_summary ----------------
#
# Postgres ``now()`` is the transaction timestamp, so rows inserted in one test transaction share a
# ``created_at``; ordering/pagination tests set it explicitly.


def _job(
    *,
    kind: str = "k",
    status: JobStatus = JobStatus.PENDING,
    created_at: datetime | None = None,
    payload: dict | None = None,
    job_id: UUID | None = None,
) -> Job:
    job = Job(kind=kind, status=status, payload=payload if payload is not None else {})
    if created_at is not None:
        job.created_at = created_at
    if job_id is not None:
        job.job_id = job_id
    return job


@pytest.mark.db
async def test_get_job_returns_job(session):
    job = _job(kind="k", payload={"x": 1})
    session.add(job)
    await session.flush()

    fetched = await get_job(session, job_id=job.job_id)

    assert fetched.job_id == job.job_id
    assert fetched.payload == {"x": 1}


@pytest.mark.db
async def test_get_job_unknown_raises(session):
    with pytest.raises(JobNotFoundError):
        await get_job(session, job_id=uuid4())


@pytest.mark.db
async def test_list_jobs_filters_by_status(session):
    session.add_all([_job(status=JobStatus.PENDING), _job(status=JobStatus.FAILED)])
    await session.flush()

    items, total = await list_jobs(session, status=JobStatus.FAILED)

    assert total == 1
    assert items[0].status is JobStatus.FAILED


@pytest.mark.db
async def test_list_jobs_filters_by_kind(session):
    session.add_all([_job(kind="a"), _job(kind="b")])
    await session.flush()

    items, total = await list_jobs(session, kind="b")

    assert total == 1
    assert items[0].kind == "b"


@pytest.mark.db
async def test_list_jobs_paginates_and_orders_newest_first(session):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(5):
        session.add(_job(status=JobStatus.FAILED, created_at=base + timedelta(minutes=i), payload={"n": i}))
    await session.flush()

    page1, total = await list_jobs(session, status=JobStatus.FAILED, limit=2, offset=0)
    page2, total2 = await list_jobs(session, status=JobStatus.FAILED, limit=2, offset=2)

    assert total == 5
    assert total2 == 5
    assert [j.payload["n"] for j in page1] == [4, 3]
    assert [j.payload["n"] for j in page2] == [2, 1]


@pytest.mark.db
async def test_list_jobs_tiebreaks_equal_created_at_by_id(session):
    # Rows enqueued in one transaction share Postgres now(); order must still be deterministic via the
    # job_id.desc() tiebreaker (production path: several jobs enqueued in one transaction).
    same = datetime(2026, 1, 1, tzinfo=UTC)
    ids = [
        UUID("00000000-0000-0000-0000-000000000001"),
        UUID("00000000-0000-0000-0000-000000000002"),
        UUID("00000000-0000-0000-0000-000000000003"),
    ]
    for job_id in ids:
        session.add(_job(status=JobStatus.FAILED, created_at=same, job_id=job_id))
    await session.flush()

    seen = []
    for offset in range(3):
        page, total = await list_jobs(session, status=JobStatus.FAILED, limit=1, offset=offset)
        assert total == 3
        assert len(page) == 1
        seen.append(page[0].job_id)

    assert seen == list(reversed(ids))  # deterministic job_id.desc()
    assert len(set(seen)) == 3


@pytest.mark.db
async def test_list_jobs_and_combines_status_and_kind(session):
    session.add_all([
        _job(kind="a", status=JobStatus.FAILED),
        _job(kind="b", status=JobStatus.FAILED),
        _job(kind="a", status=JobStatus.PENDING),
    ])
    await session.flush()

    items, total = await list_jobs(session, status=JobStatus.FAILED, kind="a")

    assert total == 1
    assert items[0].kind == "a"
    assert items[0].status is JobStatus.FAILED


@pytest.mark.db
async def test_list_jobs_offset_beyond_total_is_empty(session):
    session.add_all([_job(status=JobStatus.FAILED), _job(status=JobStatus.FAILED)])
    await session.flush()

    items, total = await list_jobs(session, status=JobStatus.FAILED, limit=10, offset=100)

    assert total == 2
    assert items == []


@pytest.mark.db
async def test_get_job_queue_summary_aggregates_by_status_and_kind(session):
    session.add_all([
        _job(kind="a", status=JobStatus.PENDING),
        _job(kind="a", status=JobStatus.PENDING),
        _job(kind="a", status=JobStatus.COMPLETED),
        _job(kind="b", status=JobStatus.FAILED),
    ])
    await session.flush()

    summary = await get_job_queue_summary(session)

    assert sum(summary.by_status.values()) == 4
    assert summary.by_status == {
        JobStatus.PENDING: 2,
        JobStatus.CLAIMED: 0,
        JobStatus.COMPLETED: 1,
        JobStatus.FAILED: 1,
    }
    by_kind = {counts.kind: counts.counts for counts in summary.by_kind}
    assert by_kind["a"] == {
        JobStatus.PENDING: 2,
        JobStatus.CLAIMED: 0,
        JobStatus.COMPLETED: 1,
        JobStatus.FAILED: 0,
    }
    assert by_kind["b"] == {
        JobStatus.PENDING: 0,
        JobStatus.CLAIMED: 0,
        JobStatus.COMPLETED: 0,
        JobStatus.FAILED: 1,
    }
    # by_kind is sorted by kind for stable output.
    assert [counts.kind for counts in summary.by_kind] == ["a", "b"]


@pytest.mark.db
async def test_get_job_queue_summary_filters_by_kind(session):
    session.add_all([
        _job(kind="a", status=JobStatus.PENDING),
        _job(kind="a", status=JobStatus.COMPLETED),
        _job(kind="b", status=JobStatus.FAILED),
    ])
    await session.flush()

    summary = await get_job_queue_summary(session, kind="a")

    assert summary.by_status == {
        JobStatus.PENDING: 1,
        JobStatus.CLAIMED: 0,
        JobStatus.COMPLETED: 1,
        JobStatus.FAILED: 0,
    }
    assert [counts.kind for counts in summary.by_kind] == ["a"]


@pytest.mark.db
async def test_get_job_queue_summary_empty_is_zero_filled(session):
    summary = await get_job_queue_summary(session)

    assert summary.by_status == {
        JobStatus.PENDING: 0,
        JobStatus.CLAIMED: 0,
        JobStatus.COMPLETED: 0,
        JobStatus.FAILED: 0,
    }
    assert summary.by_kind == []


@pytest.mark.db
async def test_claim_uses_skip_locked_for_concurrent_workers(db):
    async with db.session() as setup:
        for index in range(4):
            await enqueue_job(setup, kind="concurrent", payload={"i": index})

    async def claim_two() -> set[str]:
        async with db.session() as worker_session:
            jobs = await claim_jobs(worker_session, kinds=["concurrent"], limit=2, worker="w")
            return {str(job.job_id) for job in jobs}

    try:
        first, second = await asyncio.gather(claim_two(), claim_two())
        # No job may be claimed twice, and together both workers drain all four.
        assert first.isdisjoint(second)
        assert len(first) + len(second) == 4
    finally:
        async with db.session() as cleanup:
            await cleanup.execute(delete(Job))
