from fastapi import APIRouter

from app.api.v1.common import DBSession, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import parties as parties_service
from app.services.crm.errors import PartyNotFoundError

from .params import PartyId
from .schemas import PartyDetail, party_detail

router = APIRouter(prefix="/parties")


@router.get(
    "/{party_id}",
    dependencies=[require_scopes(Scope.CRM_READ)],
    responses=error_responses(PartyNotFoundError),
)
async def get_party(party_id: PartyId, session: DBSession) -> PartyDetail:
    """Read a party - a person or a company - with its roles and contact infos."""
    return party_detail(await parties_service.load_party(session, party_id))
