"""Lifecycle guardian: self-healing for the job queue and two-phase operations.

Neither state machine can clean up after itself on its own. If a worker dies between
``claim`` and ``complete``/``fail``, the job stays ``CLAIMED`` forever; if the bot crashes
after ``prepare``, the operation stays ``PREPARED`` forever (``EXPIRED`` is otherwise only set
lazily on the next commit attempt). The guardian runs these two passes periodically -- see the
worker in :mod:`app.workers.reaper` and the spec in ``docs/specs/lifecycle-guardian.md``.
"""

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Job, JobStatus, Operation, OperationStatus

from .jobs import fail_job

# How long a claimed job may run before its lease is considered expired and the job is
# reclaimed. A single global lease (no per-kind tuning) is enough until long-running sync
# jobs sit next to short Discord ops. No new column is needed: ``claimed_at`` carries it.
JOB_LEASE = timedelta(minutes=5)

# Recorded as ``last_error`` on jobs the guardian reclaims, so a dead-lettered job explains
# itself even though no worker ever reported a failure.
LEASE_EXPIRED_ERROR = "lease expired"


async def reap_expired_jobs(session: AsyncSession, *, batch_limit: int | None = None) -> tuple[int, int]:
    """Reclaim jobs whose worker died mid-flight (claimed but past their lease).

    Finds ``CLAIMED`` jobs with ``claimed_at`` older than :data:`JOB_LEASE`, locks them with
    ``FOR UPDATE SKIP LOCKED`` (so two guardians never reclaim the same job), and applies the
    existing :func:`app.services.bot.jobs.fail_job` reclaim transition with ``retry=True``:
    a job with attempts left returns to ``PENDING`` with backoff, an exhausted one is
    dead-lettered to ``FAILED``. ``attempt`` is *not* incremented here -- the increment paid
    on the original ``claim`` already covers this delivery.

    Returns ``(reclaimed, dead_lettered)``.
    """
    cutoff = datetime.now(UTC) - JOB_LEASE
    statement = (
        select(Job)
        .where(Job.status == JobStatus.CLAIMED, Job.claimed_at < cutoff)
        .order_by(Job.claimed_at)
        .with_for_update(skip_locked=True)
    )
    if batch_limit is not None:
        statement = statement.limit(batch_limit)

    jobs = list((await session.execute(statement)).scalars().all())

    reclaimed = 0
    dead_lettered = 0
    for job in jobs:
        updated = await fail_job(session, job_id=job.job_id, error=LEASE_EXPIRED_ERROR, retry=True)
        if updated.status is JobStatus.FAILED:
            dead_lettered += 1
        else:
            reclaimed += 1

    return reclaimed, dead_lettered


async def sweep_expired_operations(session: AsyncSession) -> int:
    """Materialize expired two-phase reservations to ``EXPIRED``.

    Flips ``PREPARED`` operations whose ``expires_at`` has passed in a single bulk update.
    Terminal rows (``COMMITTED``/``FAILED``/``EXPIRED``) are untouched, and the marking is
    idempotent: it never collides with the lazy ``EXPIRED`` transition in ``commit`` because
    the ``status = PREPARED`` predicate simply won't match an already-flipped row. The freed
    capacity slot was already passively available (capacity counts only reservations with
    ``expires_at`` still in the future).

    Returns the number of operations expired.
    """
    statement = (
        update(Operation)
        .where(Operation.status == OperationStatus.PREPARED, Operation.expires_at <= datetime.now(UTC))
        .values(status=OperationStatus.EXPIRED)
    )
    # execute() is typed as Result, but a Core UPDATE always yields a CursorResult with rowcount.
    result = cast(CursorResult, await session.execute(statement))
    return result.rowcount
