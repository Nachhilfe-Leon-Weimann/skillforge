from fastapi import APIRouter, Response, status

from app.api.v1.common import DBSession, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import companies as companies_service
from app.services.crm.errors import CompanyNotFoundError

from .params import PARTY_LOCATION, PartyId
from .schemas import CompanyCreateRequest, CompanyDetail, CompanyUpdateRequest

router = APIRouter(prefix="/companies")


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[require_scopes(Scope.CRM_WRITE)])
async def create_company(request: CompanyCreateRequest, response: Response, session: DBSession) -> CompanyDetail:
    """Create a company, optionally together with contact infos. The `Location` header points to the party."""
    party = await companies_service.create_company(
        session, name=request.name, contact_infos=request.contact_info_inputs()
    )
    response.headers["Location"] = PARTY_LOCATION.format(party_id=party.id)
    return CompanyDetail.from_model(party)


@router.patch(
    "/{party_id}",
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(CompanyNotFoundError),
)
async def update_company(party_id: PartyId, request: CompanyUpdateRequest, session: DBSession) -> CompanyDetail:
    """Change a company. Only the fields that are sent change; an empty body changes nothing."""
    party = await companies_service.update_company(session, party_id, **request.model_dump())
    return CompanyDetail.from_model(party)
