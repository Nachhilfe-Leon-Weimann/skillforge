from fastapi import APIRouter, status

from app.api.v1.common import DBSession, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import contact_infos as contact_infos_service
from app.services.crm.errors import (
    ContactInfoAlreadyExistsError,
    ContactInfoNotFoundError,
    InvalidContactValueError,
    PartyNotFoundError,
)

from .params import ContactInfoId, PartyId
from .schemas import ContactInfoCreateRequest, ContactInfoResponse, ContactInfoUpdateRequest

router = APIRouter(prefix="/parties/{party_id}/contact-infos")


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(PartyNotFoundError, ContactInfoAlreadyExistsError),
)
async def add_contact_info(
    party_id: PartyId, request: ContactInfoCreateRequest, session: DBSession
) -> ContactInfoResponse:
    """Add a contact info to a party. The same type and value cannot be added twice."""
    contact_info = await contact_infos_service.add_contact_info(session, party_id, **request.model_dump())
    return ContactInfoResponse.from_model(contact_info)


@router.patch(
    "/{contact_info_id}",
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(ContactInfoNotFoundError, ContactInfoAlreadyExistsError, InvalidContactValueError),
)
async def update_contact_info(
    party_id: PartyId, contact_info_id: ContactInfoId, request: ContactInfoUpdateRequest, session: DBSession
) -> ContactInfoResponse:
    """Change the value or the label of a contact info; its type is immutable. An empty body changes nothing."""
    contact_info = await contact_infos_service.update_contact_info(
        session, party_id, contact_info_id, **request.model_dump()
    )
    return ContactInfoResponse.from_model(contact_info)


@router.delete(
    "/{contact_info_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(ContactInfoNotFoundError),
)
async def remove_contact_info(party_id: PartyId, contact_info_id: ContactInfoId, session: DBSession) -> None:
    """Remove a contact info from a party."""
    await contact_infos_service.remove_contact_info(session, party_id, contact_info_id)
