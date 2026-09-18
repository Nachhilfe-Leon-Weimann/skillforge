from fastapi import APIRouter, status

from app.api.v1.common import DBSession, Page, error_responses
from app.core.auth import Scope, require_scopes
from app.services.crm import relations as relations_service
from app.services.crm.errors import (
    InvalidPartyRelationError,
    PartyNotFoundError,
    PartyRelationNotFoundError,
    RelatedPartyNotFoundError,
)

from .params import PartyId, RelationListQuery, RelationType, ToPartyId
from .schemas import RelationResponse

router = APIRouter(prefix="/parties/{party_id}/relations")


@router.get(
    "",
    dependencies=[require_scopes(Scope.CRM_READ)],
    responses=error_responses(PartyNotFoundError),
)
async def list_relations(party_id: PartyId, params: RelationListQuery, session: DBSession) -> Page[RelationResponse]:
    """List the relations of a party in both directions, oldest first; each names the party on the other side."""
    relations, total = await relations_service.list_relations(session, party_id, **params.model_dump())
    return Page.of([RelationResponse.from_view(relation) for relation in relations], total=total, params=params)


@router.put(
    "/{type}/{to_party_id}",
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(PartyNotFoundError, RelatedPartyNotFoundError, InvalidPartyRelationError),
)
async def put_relation(
    party_id: PartyId, type: RelationType, to_party_id: ToPartyId, session: DBSession
) -> RelationResponse:
    """Relate two parties: `{party_id}` is `{type}` `{to_party_id}`. Idempotent.

    `parent_of` needs a person on both sides, `tutor_of` a tutor and a student, and `pays_for` points
    to a person. The answer names the other party, which confirms the pasted ID.
    """
    return RelationResponse.from_view(await relations_service.put_relation(session, party_id, type, to_party_id))


@router.delete(
    "/{type}/{to_party_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_scopes(Scope.CRM_WRITE)],
    responses=error_responses(PartyRelationNotFoundError),
)
async def remove_relation(party_id: PartyId, type: RelationType, to_party_id: ToPartyId, session: DBSession) -> None:
    """Remove the relation `{party_id}` is `{type}` `{to_party_id}`."""
    await relations_service.remove_relation(session, party_id, type, to_party_id)
