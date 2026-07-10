"""Forge-first job queue.

Forge enqueues jobs; SkillBot claims them, runs them, and reports completion or failure.

**Delivery is at-least-once.** A claimed job whose worker dies is reclaimed once its lease
expires (see :mod:`app.services.bot.reaper`) and handed out again, so the same job can be
delivered more than once. Consumers must therefore make their handlers idempotent -- a
re-delivered job must not trigger a duplicate side effect (e.g. a second Discord action).
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Job, JobStatus

from .errors import JobNotClaimedError, JobNotFailedError, JobNotFoundError
from .views import JobKindCountsView, JobQueueSummaryView

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

    Uses ``FOR UPDATE SKIP LOCKED`` so concurrent workers never claim the same job. Delivery
    is at-least-once: a claimed job whose lease expires is reclaimed and re-delivered, so the
    claiming handler must be idempotent.
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


async def requeue_job(session: AsyncSession, *, job_id: uuid.UUID) -> Job:
    """Reset a dead-lettered (``FAILED``) job to ``PENDING`` so it is claimable again now.

    Operator recovery path for the dead-letter queue (lifecycle guardian spec, P1). Clears the
    failure and claim bookkeeping and resets ``attempt`` to 0, so the job starts a fresh
    delivery cycle. Raises :class:`JobNotFoundError` if the id is unknown and
    :class:`JobNotFailedError` if the job is not in ``FAILED`` (only dead-letters are requeued).
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"No job with id {job_id}")
    if job.status is not JobStatus.FAILED:
        raise JobNotFailedError(f"Job {job_id} is {job.status.value}, not failed; only failed jobs can be requeued")

    job.status = JobStatus.PENDING
    job.attempt = 0
    job.available_at = datetime.now(UTC)
    job.claimed_at = None
    job.claimed_by = None
    job.failed_at = None
    job.last_error = None
    await session.flush()
    return job


async def list_dead_lettered_jobs(session: AsyncSession) -> list[Job]:
    """Return all dead-lettered (``FAILED``) jobs, most recently failed first.

    Read-only operator view of the dead-letter queue (lifecycle guardian spec, P1).
    """
    statement = select(Job).where(Job.status == JobStatus.FAILED).order_by(Job.failed_at.desc())
    return list((await session.execute(statement)).scalars().all())


async def get_job(session: AsyncSession, *, job_id: uuid.UUID) -> Job:
    """Return a single job by id, including its ``payload``.

    Read-plane counterpart to the claim/complete/fail write path. Raises :class:`JobNotFoundError`
    if no job has that id.
    """
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"No job with id {job_id}")
    return job


async def list_jobs(
    session: AsyncSession,
    *,
    status: JobStatus | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Job], int]:
    """List jobs matching the given filters, newest first, with the total match count.

    All filters are optional and AND-combined. Returns ``(page, total)`` where ``page`` is at most
    ``limit`` jobs starting at ``offset`` and ``total`` is the number of jobs matching the filters
    regardless of pagination.
    """
    filters = []
    if status is not None:
        filters.append(Job.status == status)
    if kind is not None:
        filters.append(Job.kind == kind)

    total = (await session.execute(select(func.count()).select_from(Job).where(*filters))).scalar_one()

    statement = (
        select(Job).where(*filters).order_by(Job.created_at.desc(), Job.job_id.desc()).limit(limit).offset(offset)
    )
    jobs = (await session.execute(statement)).scalars().all()
    return jobs, total


async def get_job_queue_summary(session: AsyncSession) -> JobQueueSummaryView:
    """Summarize the job queue as a funnel: overall counts by status plus a per-kind breakdown.

    Both the overall ``by_status`` map and each kind's counts are zero-filled across every
    :class:`JobStatus`, so a status that no job currently occupies still reports ``0`` (no silent
    gaps). ``by_kind`` is sorted by kind. One ``GROUP BY (kind, status)`` query, aggregated in Python.
    """
    rows = (await session.execute(select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status))).all()

    by_status = {status: 0 for status in JobStatus}
    per_kind: dict[str, dict[JobStatus, int]] = {}
    total = 0
    for kind, status, count in rows:
        total += count
        by_status[status] += count
        per_kind.setdefault(kind, {member: 0 for member in JobStatus})[status] += count

    by_kind = [JobKindCountsView(kind=kind, counts=counts) for kind, counts in sorted(per_kind.items())]
    return JobQueueSummaryView(total=total, by_status=by_status, by_kind=by_kind)


async def _get_claimed_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError("Job not found")
    if job.status is not JobStatus.CLAIMED:
        raise JobNotClaimedError("Job is not in a claimed state")
    return job
