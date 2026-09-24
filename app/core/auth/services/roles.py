"""The roles the CRM derives (user-authentication spec, decision K): ``student``, ``tutor`` and ``guardian``
are never stored.

This module reads the CRM *models*; it never imports ``app.services`` (ADR 0007).
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import PartyRelation, Student, Tutor

from ..reach import DELEGATION_RELATION_TYPES
from ..roles import Role


async def derive_roles(session: AsyncSession, party_id: uuid.UUID) -> frozenset[Role]:
    """Return the CRM-derived roles of one party."""
    return (await derive_roles_for(session, [party_id]))[party_id]


async def derive_roles_for(session: AsyncSession, party_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, frozenset[Role]]:
    """Return the CRM-derived roles of every given party, in three queries for the whole set.

    A party with a ``Student`` row is a ``student``, one with a ``Tutor`` row a ``tutor``, and one
    with an outgoing relation in ``DELEGATION_RELATION_TYPES`` a ``guardian``. Every given party is a
    key of the result, one without a derived role maps to the empty set.
    """
    ids = frozenset(party_ids)
    if not ids:
        return {}

    found: dict[uuid.UUID, set[Role]] = {party_id: set() for party_id in ids}
    queries = {
        Role.STUDENT: select(Student.person_id).where(Student.person_id.in_(ids)),
        Role.TUTOR: select(Tutor.person_id).where(Tutor.person_id.in_(ids)),
        Role.GUARDIAN: select(PartyRelation.from_party_id)
        .where(PartyRelation.from_party_id.in_(ids), PartyRelation.type.in_(DELEGATION_RELATION_TYPES))
        .distinct(),
    }
    for role, query in queries.items():
        for party_id in await session.scalars(query):
            found[party_id].add(role)

    return {party_id: frozenset(roles) for party_id, roles in found.items()}
