import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
from app.core.db.dependencies import get_db_session
from app.services.bot import (
    JobNotClaimedError,
    JobNotFoundError,
    claim_jobs,
    complete_job,
    fail_job,
)

from .dependencies import BotWrite
from .schemas import BotJob, JobClaimRequest, JobFailRequest, JobResponse

router = APIRouter(prefix="/jobs")

JOB_NOT_FOUND = "Job not found"
JOB_NOT_CLAIMED = "Job is not in a claimed state"


@router.post("/claim", response_model=list[BotJob])
async def claim_jobs_endpoint(
    request: JobClaimRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotWrite,
) -> list[BotJob]:
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
    try:
        job = await fail_job(session, job_id=job_id, error=request.error, retry=request.retry)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=JOB_NOT_FOUND) from exc
    except JobNotClaimedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=JOB_NOT_CLAIMED) from exc

    return JobResponse.from_model(job)
