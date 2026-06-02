import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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
