from fastapi import APIRouter, Response, status

from app.api.v1.common import DBSession, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import persons as persons_service
from app.services.crm.errors import PersonNotFoundError

from .params import PARTY_LOCATION, PartyId
from .schemas import PersonCreateRequest, PersonDetail, PersonUpdateRequest

router = APIRouter(prefix="/persons")


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[require_scopes(Scope.CRM_WRITE)])
async def create_person(request: PersonCreateRequest, response: Response, session: DBSession) -> PersonDetail:
    """Create a person, optionally together with contact infos.

    The `Location` header points to the party. To avoid duplicates, search first:
    `GET /parties?q=<e-mail>`.
    """
    party = await persons_service.create_person(
        session,
        firstname=request.firstname,
        lastname=request.lastname,
        contact_infos=request.contact_info_inputs(),
    )
    response.headers["Location"] = PARTY_LOCATION.format(party_id=party.id)
    return PersonDetail.from_model(party)


@router.patch(
    "/{party_id}",
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(PersonNotFoundError),
)
async def update_person(party_id: PartyId, request: PersonUpdateRequest, session: DBSession) -> PersonDetail:
    """Change a person. Only the fields that are sent change; an empty body changes nothing."""
    party = await persons_service.update_person(session, party_id, **request.model_dump())
    return PersonDetail.from_model(party)
