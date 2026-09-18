from fastapi import APIRouter, status

from app.api.v1.common import DBSession, Page, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import parties as parties_service
from app.services.crm.errors import PartyInUseError, PartyNotFoundError

from .params import PartyId, PartyListQuery
from .schemas import PartyDetail, PartyListItem, party_detail

router = APIRouter(prefix="/parties")


@router.get("", dependencies=[require_scopes(Scope.CRM_READ)])
async def list_parties(params: PartyListQuery, session: DBSession) -> Page[PartyListItem]:
    """List and search parties, ordered by name (persons by last name) regardless of case."""
    parties, total = await parties_service.list_parties(session, **params.model_dump())
    return Page.of([PartyListItem.from_model(party) for party in parties], total=total, params=params)


@router.get(
    "/{party_id}",
    dependencies=[require_scopes(Scope.CRM_READ)],
    responses=error_responses(PartyNotFoundError),
)
async def get_party(party_id: PartyId, session: DBSession) -> PartyDetail:
    """Read a party - a person or a company - with its roles and contact infos."""
    return party_detail(await parties_service.load_party(session, party_id))


@router.delete(
    "/{party_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(PartyNotFoundError, PartyInUseError),
)
async def delete_party(party_id: PartyId, session: DBSession) -> None:
    """Delete a party together with its roles, contact infos and relations.

    Refused while a Discord account or an external system (sevDesk, Clockodo, Microsoft) is linked to
    the party: remove those links first, in the system that owns them.
    """
    await parties_service.delete_party(session, party_id)
