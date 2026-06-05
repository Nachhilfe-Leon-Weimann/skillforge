import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.core.db.models import Job, JobStatus
from app.services.bot import claim_jobs, enqueue_job
from app.services.bot.reaper import (
    JOB_LEASE,
    LEASE_EXPIRED_ERROR,
    reap_expired_jobs,
)


async def _stale_claimed_job(session, *, max_attempts: int = 5, lease_age: timedelta = timedelta(minutes=10)) -> Job:
    """Enqueue a job, claim it, then back-date its lease so the reaper treats it as expired."""
    await enqueue_job(session, kind="t", max_attempts=max_attempts)
    [job] = await claim_jobs(session, limit=1, worker="dead-worker")
    job.claimed_at = datetime.now(UTC) - lease_age
    await session.flush()
    return job


# --- Job reaper -------------------------------------------------------------


@pytest.mark.db
async def test_reap_reclaims_expired_lease(session):
    job = await _stale_claimed_job(session)

    reclaimed, dead_lettered = await reap_expired_jobs(session)

    assert (reclaimed, dead_lettered) == (1, 0)
    await session.refresh(job)
    assert job.status is JobStatus.PENDING
    assert job.available_at > datetime.now(UTC)
    assert job.claimed_at is None
    assert job.claimed_by is None
    assert job.last_error == LEASE_EXPIRED_ERROR


@pytest.mark.db
async def test_reap_does_not_increment_attempt(session):
    job = await _stale_claimed_job(session)
    assert job.attempt == 1  # incremented once on claim

    await reap_expired_jobs(session)

    await session.refresh(job)
    assert job.attempt == 1  # the reaper must not add another attempt


@pytest.mark.db
async def test_reap_leaves_jobs_within_lease_untouched(session):
    job = await _stale_claimed_job(session, lease_age=JOB_LEASE - timedelta(minutes=1))

    reclaimed, dead_lettered = await reap_expired_jobs(session)

    assert (reclaimed, dead_lettered) == (0, 0)
    await session.refresh(job)
    assert job.status is JobStatus.CLAIMED
    assert job.claimed_by == "dead-worker"


@pytest.mark.db
async def test_reap_dead_letters_when_attempts_exhausted(session):
    job = await _stale_claimed_job(session, max_attempts=1)  # claim drives attempt to max

    reclaimed, dead_lettered = await reap_expired_jobs(session)

    assert (reclaimed, dead_lettered) == (0, 1)
    await session.refresh(job)
    assert job.status is JobStatus.FAILED
    assert job.failed_at is not None
    assert job.last_error == LEASE_EXPIRED_ERROR


@pytest.mark.db
async def test_reap_empty_queue_returns_zero(session):
    assert await reap_expired_jobs(session) == (0, 0)


@pytest.mark.db
async def test_reap_mixes_reclaim_and_dead_letter_in_one_run(session):
    retryable = await _stale_claimed_job(session, max_attempts=5)
    exhausted = await _stale_claimed_job(session, max_attempts=1)

    reclaimed, dead_lettered = await reap_expired_jobs(session)

    assert (reclaimed, dead_lettered) == (1, 1)
    await session.refresh(retryable)
    await session.refresh(exhausted)
    assert retryable.status is JobStatus.PENDING
    assert exhausted.status is JobStatus.FAILED


@pytest.mark.db
async def test_reap_uses_skip_locked_for_concurrent_guardians(db):
    async with db.session() as setup:
        for _ in range(4):
            await enqueue_job(setup, kind="reapme")
        jobs = await claim_jobs(setup, kinds=["reapme"], limit=4, worker="dead")
        stale = datetime.now(UTC) - timedelta(minutes=10)
        for job in jobs:
            job.claimed_at = stale
        await setup.flush()

    async def reap() -> int:
        async with db.session() as guardian:
            reclaimed, dead_lettered = await reap_expired_jobs(guardian, batch_limit=2)
            return reclaimed + dead_lettered

    try:
        first, second = await asyncio.gather(reap(), reap())
        # With a batch limit of 2 over 4 stale jobs, SKIP LOCKED must hand each guardian its
        # own disjoint half: both make progress (2 each) and together they reclaim all four
        # exactly once. Without SKIP LOCKED one guardian would block or double-process.
        assert first == 2
        assert second == 2
    finally:
        async with db.session() as cleanup:
            await cleanup.execute(delete(Job))
