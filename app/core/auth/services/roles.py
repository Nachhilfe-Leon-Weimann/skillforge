"""Derivation of the roles the CRM knows (ADR 0008, decision J).

``student``, ``tutor`` and ``guardian`` follow from CRM data and are never stored; only ``admin``
is a row in ``auth.user_account_role``. Reading the CRM *models* is allowed here - importing
``app.services`` is not, so this module talks to the tables, never to the CRM services.
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import PartyRelation, PartyRelationType, Student, Tutor

from ..roles import Role

# The outgoing relations that make a party a guardian of another. The same pair the bot's
# delegation check uses; P0-7 replaces both with DELEGATION_RELATION_TYPES in ``reach.py``, which
# app/core/auth may own but app/services/bot/authz.py may not export to it.
GUARDIAN_RELATION_TYPES = (PartyRelationType.PARENT_OF, PartyRelationType.PAYS_FOR)


async def derive_roles(session: AsyncSession, party_id: uuid.UUID) -> frozenset[Role]:
    """Return the CRM-derived roles of one party."""
    return (await derive_roles_for(session, [party_id])).get(party_id, frozenset())


async def derive_roles_for(session: AsyncSession, party_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, frozenset[Role]]:
    """Return the CRM-derived roles of every given party, in three queries for the whole set.

    A party without any derived role maps to the empty set, so a caller can index the result for
    every ID it asked about.
    """
    ids = frozenset(party_ids)
    if not ids:
        return {}

    found: dict[uuid.UUID, set[Role]] = {party_id: set() for party_id in ids}
    for party_id in await session.scalars(select(Student.person_id).where(Student.person_id.in_(ids))):
        found[party_id].add(Role.STUDENT)
    for party_id in await session.scalars(select(Tutor.person_id).where(Tutor.person_id.in_(ids))):
        found[party_id].add(Role.TUTOR)
    guardians = await session.scalars(
        select(PartyRelation.from_party_id)
        .where(PartyRelation.from_party_id.in_(ids), PartyRelation.type.in_(GUARDIAN_RELATION_TYPES))
        .distinct()
    )
    for party_id in guardians:
        found[party_id].add(Role.GUARDIAN)

    return {party_id: frozenset(roles) for party_id, roles in found.items()}
