import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Party, PartyRelationType

from .principals import get_principal_view

# Relations that let a principal act on behalf of another party (the relation's ``to_party``).
# Tutor authority runs through role/grants, not delegation, so ``TUTOR_OF`` is intentionally absent.
DELEGATION_RELATION_TYPES = (PartyRelationType.PARENT_OF, PartyRelationType.PAYS_FOR)


def _allowed_target_parties(party: Party | None) -> set[uuid.UUID]:
    """The set of parties a principal may act on: its own party plus its delegated parties."""
    if party is None:
        return set()
    allowed = {party.id}
    for relation in party.outgoing_relations:
        if relation.type in DELEGATION_RELATION_TYPES:
            allowed.add(relation.to_party_id)
    return allowed


async def check_authorization(
    session: AsyncSession,
    *,
    actor_discord_id: int,
    action_key: str,
    target_party_id: uuid.UUID,
) -> bool:
    """Decide whether ``actor_discord_id`` may perform ``action_key`` on ``target_party_id``.

    Combines the two authorization layers: the grant engine answers *whether* the actor may perform
    the action (guild-agnostic ``USER``/``GROUP`` grants, via the principal view), and ``PartyRelation``
    answers *on whose behalf* -- the actor may act on its own party plus the ``to_party`` of its
    outgoing ``PARENT_OF`` / ``PAYS_FOR`` relations. Role-scoped (guild-specific) grants are out of
    scope, consistent with the guild-agnostic principal lookup.

    An inactive actor is denied. Raises :class:`PrincipalNotFoundError` if the actor is not a
    registered principal.
    """
    view = await get_principal_view(session, actor_discord_id)
    if not view.user.active:
        return False
    if action_key not in view.permission_keys:
        return False
    return target_party_id in _allowed_target_parties(view.party)
