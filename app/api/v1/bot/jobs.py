import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
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

JOB_NOT_FOUND = "Job not found"
JOB_NOT_CLAIMED = "Job is not in a claimed state"


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
    responses={
        404: error_response(JOB_NOT_FOUND),
        409: error_response(JOB_NOT_CLAIMED),
    },
)
async def complete_job_endpoint(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> JobResponse:
    try:
        job = await complete_job(session, job_id=job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND) from exc
    except JobNotClaimedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=JOB_NOT_CLAIMED) from exc

    return JobResponse.from_model(job)


@router.post(
    "/{job_id}/fail",
    response_model=JobResponse,
    responses={
        404: error_response(JOB_NOT_FOUND),
        409: error_response(JOB_NOT_CLAIMED),
    },
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
    try:
        job = await fail_job(session, job_id=job_id, error=request.error, retry=request.retry)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND) from exc
    except JobNotClaimedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=JOB_NOT_CLAIMED) from exc

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
) -> JobQueueSummary:
    """Queue funnel: overall counts by status plus a per-kind breakdown, for operator observability."""
    summary = await get_job_queue_summary(session)
    return JobQueueSummary.from_view(summary)


@router.get("/{job_id}", response_model=JobDetail, responses={404: error_response(JOB_NOT_FOUND)})
async def read_job(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> JobDetail:
    """Read a single job by id, including its `payload`."""
    try:
        job = await get_job(session, job_id=job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND) from exc

    return JobDetail.from_model(job)
