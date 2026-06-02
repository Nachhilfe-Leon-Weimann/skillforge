import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Job, JobStatus

from .errors import JobNotClaimedError, JobNotFoundError

# Delay before a failed-but-retryable job becomes claimable again.
RETRY_BACKOFF = timedelta(seconds=60)


async def enqueue_job(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict[str, Any] | None = None,
    available_at: datetime | None = None,
    max_attempts: int = 5,
) -> Job:
    """Enqueue a job for SkillBot to claim. Called by Forge-internal workflows (e.g. activations)."""
    job = Job(kind=kind, payload=payload or {}, max_attempts=max_attempts)
    if available_at is not None:
        job.available_at = available_at
    session.add(job)
    await session.flush()
    return job


async def claim_jobs(
    session: AsyncSession,
    *,
    kinds: list[str] | None = None,
    limit: int = 1,
    worker: str | None = None,
) -> list[Job]:
    """Atomically claim up to ``limit`` due pending jobs.

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never claim the same job.
    """
    now = datetime.now(UTC)
    statement = select(Job).where(Job.status == JobStatus.PENDING, Job.available_at <= now)
    if kinds:
        statement = statement.where(Job.kind.in_(kinds))
    statement = statement.order_by(Job.available_at, Job.created_at).limit(limit).with_for_update(skip_locked=True)

    jobs = list((await session.execute(statement)).scalars().all())
    for job in jobs:
        job.status = JobStatus.CLAIMED
        job.attempt += 1
        job.claimed_at = now
        job.claimed_by = worker
    await session.flush()
    return jobs


async def complete_job(session: AsyncSession, *, job_id: uuid.UUID) -> Job:
    job = await _get_claimed_job(session, job_id)
    job.status = JobStatus.COMPLETED
    job.completed_at = datetime.now(UTC)
    await session.flush()
    return job


async def fail_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    error: str | None = None,
    retry: bool = False,
) -> Job:
    """Mark a claimed job as failed. If ``retry`` and attempts remain, requeue it with backoff."""
    job = await _get_claimed_job(session, job_id)
    job.last_error = error
    if retry and job.attempt < job.max_attempts:
        job.status = JobStatus.PENDING
        job.available_at = datetime.now(UTC) + RETRY_BACKOFF
        job.claimed_at = None
        job.claimed_by = None
    else:
        job.status = JobStatus.FAILED
        job.failed_at = datetime.now(UTC)
    await session.flush()
    return job


async def _get_claimed_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError("Job not found")
    if job.status is not JobStatus.CLAIMED:
        raise JobNotClaimedError("Job is not in a claimed state")
    return job
