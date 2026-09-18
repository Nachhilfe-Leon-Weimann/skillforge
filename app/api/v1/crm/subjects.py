from fastapi import APIRouter, status

from app.api.v1.common import DBSession, Page, PageQuery, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import subjects as subjects_service
from app.services.crm.errors import SubjectAlreadyExistsError, SubjectInUseError, SubjectNotFoundError

from .params import SubjectId
from .schemas import SubjectCreateRequest, SubjectResponse, SubjectUpdateRequest

router = APIRouter(prefix="/subjects")


@router.get("", dependencies=[require_scopes(Scope.CRM_READ)])
async def list_subjects(params: PageQuery, session: DBSession) -> Page[SubjectResponse]:
    """List subjects, ordered by title."""
    subjects, total = await subjects_service.list_subjects(session, **params.model_dump())
    return Page.of([SubjectResponse.from_model(subject) for subject in subjects], total=total, params=params)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(SubjectAlreadyExistsError),
)
async def create_subject(request: SubjectCreateRequest, session: DBSession) -> SubjectResponse:
    """Create a subject. Titles are unique regardless of case."""
    return SubjectResponse.from_model(await subjects_service.create_subject(session, **request.model_dump()))


@router.patch(
    "/{subject_id}",
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(SubjectNotFoundError, SubjectAlreadyExistsError),
)
async def update_subject(subject_id: SubjectId, request: SubjectUpdateRequest, session: DBSession) -> SubjectResponse:
    """Change a subject. Only the fields that are sent change; an empty body changes nothing."""
    subject = await subjects_service.update_subject(session, subject_id, **request.model_dump())
    return SubjectResponse.from_model(subject)


@router.delete(
    "/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(SubjectNotFoundError, SubjectInUseError),
)
async def delete_subject(subject_id: SubjectId, session: DBSession) -> None:
    """Delete a subject that is not assigned to any student or tutor."""
    await subjects_service.delete_subject(session, subject_id)
