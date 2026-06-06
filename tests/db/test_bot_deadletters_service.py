from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.db.models import JobStatus
from app.services.bot import (
    JobNotFailedError,
    JobNotFoundError,
    claim_jobs,
    enqueue_job,
    fail_job,
    list_dead_lettered_jobs,
    requeue_job,
)


async def _dead_lettered_job(session, *, kind: str = "t", error: str = "boom"):
    """Enqueue, claim, and dead-letter a job so it lands in ``FAILED``."""
    await enqueue_job(session, kind=kind, max_attempts=1)
    [job] = await claim_jobs(session, limit=1, worker="dead-worker")
    return await fail_job(session, job_id=job.job_id, error=error, retry=False)


# --- Requeue -----------------------------------------------------------------


@pytest.mark.db
async def test_requeue_resets_failed_job(session):
    job = await _dead_lettered_job(session)
    assert job.status is JobStatus.FAILED

    requeued = await requeue_job(session, job_id=job.job_id)

    assert requeued.status is JobStatus.PENDING
    assert requeued.attempt == 0
    assert requeued.available_at <= datetime.now(UTC)
    assert requeued.failed_at is None
    assert requeued.last_error is None
    assert requeued.claimed_at is None
    assert requeued.claimed_by is None


@pytest.mark.db
async def test_requeued_job_is_immediately_claimable(session):
    job = await _dead_lettered_job(session)

    await requeue_job(session, job_id=job.job_id)

    [claimed] = await claim_jobs(session, limit=1)
    assert claimed.job_id == job.job_id
    assert claimed.attempt == 1  # a fresh delivery cycle starts from zero


@pytest.mark.db
async def test_requeue_unknown_job_raises(session):
    with pytest.raises(JobNotFoundError):
        await requeue_job(session, job_id=uuid4())


@pytest.mark.db
async def test_requeue_pending_job_raises(session):
    job = await enqueue_job(session, kind="t")

    with pytest.raises(JobNotFailedError):
        await requeue_job(session, job_id=job.job_id)


@pytest.mark.db
async def test_requeue_claimed_job_raises(session):
    await enqueue_job(session, kind="t")
    [job] = await claim_jobs(session, limit=1)

    with pytest.raises(JobNotFailedError):
        await requeue_job(session, job_id=job.job_id)


# --- List --------------------------------------------------------------------


@pytest.mark.db
async def test_list_returns_only_failed_jobs(session):
    failed = await _dead_lettered_job(session, kind="failed-kind", error="kaboom")
    await enqueue_job(session, kind="pending-kind")  # PENDING -> excluded
    await enqueue_job(session, kind="claimed-kind")
    await claim_jobs(session, kinds=["claimed-kind"], limit=1)  # CLAIMED -> excluded

    jobs = await list_dead_lettered_jobs(session)

    assert [job.job_id for job in jobs] == [failed.job_id]
    assert jobs[0].kind == "failed-kind"
    assert jobs[0].last_error == "kaboom"
    assert jobs[0].failed_at is not None


@pytest.mark.db
async def test_list_orders_most_recently_failed_first(session):
    older = await _dead_lettered_job(session, kind="older")
    await _dead_lettered_job(session, kind="newer")
    older.failed_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    jobs = await list_dead_lettered_jobs(session)

    assert [job.kind for job in jobs] == ["newer", "older"]


@pytest.mark.db
async def test_list_empty_when_no_dead_letters(session):
    await enqueue_job(session, kind="t")  # PENDING only

    assert await list_dead_lettered_jobs(session) == []
