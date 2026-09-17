import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.db.dependencies import get_db_session
from app.core.db.models import JobStatus
from app.services.bot import (
    JobNotClaimedError,
    JobNotFoundError,
    claim_jobs,
    complete_job,
    fail_job,
    get_job,
    get_job_queue_summary,
    list_jobs,
)

from .dependencies import BotRead, BotWrite
from .schemas import (
    BotJob,
    JobClaimRequest,
    JobDetail,
    JobFailRequest,
    JobListItem,
    JobPage,
    JobQueueSummary,
    JobResponse,
)

router = APIRouter(prefix="/jobs")


@router.post("/claim", response_model=list[BotJob])
async def claim_jobs_endpoint(
    request: JobClaimRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> list[BotJob]:
    """Claim up to `limit` due jobs for processing.

    **Delivery is at-least-once.** A claimed job whose worker dies is reclaimed once its lease
    (5 minutes) expires and handed out again, so the same job can be delivered more than once.
    Re-delivery is real, not hypothetical -- handlers **must be idempotent** and must not
    trigger a duplicate side effect on a job they have already processed.
    """
    jobs = await claim_jobs(session, kinds=request.kinds, limit=request.limit, worker=request.worker)
    return [BotJob.from_model(job) for job in jobs]


@router.post(
    "/{job_id}/complete",
    response_model=JobResponse,
    responses=error_responses(JobNotFoundError, JobNotClaimedError),
)
async def complete_job_endpoint(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> JobResponse:
    job = await complete_job(session, job_id=job_id)
    return JobResponse.from_model(job)


@router.post(
    "/{job_id}/fail",
    response_model=JobResponse,
    responses=error_responses(JobNotFoundError, JobNotClaimedError),
)
async def fail_job_endpoint(
    job_id: uuid.UUID,
    request: JobFailRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> JobResponse:
    """Report a claimed job as failed.

    With `retry` set and attempts remaining, the job is requeued with backoff; otherwise it is
    dead-lettered to `failed`. A worker that crashes without reporting reaches the same outcome
    automatically once its lease expires (see the at-least-once contract on `claim`).
    """
    job = await fail_job(session, job_id=job_id, error=request.error, retry=request.retry)
    return JobResponse.from_model(job)


@router.get("", response_model=JobPage)
async def list_jobs_endpoint(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobPage:
    """List jobs by status / kind, newest first. List items omit the `payload`; read a job by id for it."""
    jobs, total = await list_jobs(session, status=status_filter, kind=kind, limit=limit, offset=offset)
    return JobPage(
        items=[JobListItem.from_model(job) for job in jobs],
        total=total,
        limit=limit,
        offset=offset,
    )


# Declared before ``/{job_id}`` so the static path is matched first.
@router.get("/summary", response_model=JobQueueSummary)
async def job_queue_summary_endpoint(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
    kind: Annotated[str | None, Query(min_length=1)] = None,
) -> JobQueueSummary:
    """Queue funnel (depth + status counts, globally and per kind) for operator observability.

    Passing `kind` scopes the whole summary -- top-level totals and the `by_kind` list -- to that
    exact job kind.
    """
    summary = await get_job_queue_summary(session, kind=kind)
    return JobQueueSummary.from_view(summary)


@router.get("/{job_id}", response_model=JobDetail, responses=error_responses(JobNotFoundError))
async def read_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> JobDetail:
    """Read a single job by id, including its `payload`."""
    job = await get_job(session, job_id=job_id)
    return JobDetail.from_model(job)
