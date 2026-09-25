"""`resolve_reach` against the real database: the basis of each party, and parity with the bot's delegation check."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import Access, ReachBasis
from app.core.auth.reach import resolve_reach
from app.core.db.models import Party, PartyRelation, PartyRelationType, PartyType
from app.services.bot.authz import _allowed_target_parties

pytestmark = pytest.mark.db


async def _parties(session: AsyncSession, count: int) -> list[uuid.UUID]:
    parties = [Party(type=PartyType.PERSON) for _ in range(count)]
    session.add_all(parties)
    await session.flush()
    return [party.id for party in parties]


async def _relate(session: AsyncSession, *relations: tuple[uuid.UUID, PartyRelationType, uuid.UUID]) -> None:
    session.add_all(
        PartyRelation(from_party_id=from_party, type=type, to_party_id=to_party)
        for from_party, type, to_party in relations
    )
    await session.flush()


async def test_the_basis_is_self_for_the_own_party_and_guardian_for_both_children(session: AsyncSession):
    mother, child, paid_for, stranger = await _parties(session, 4)
    await _relate(
        session,
        (mother, PartyRelationType.PARENT_OF, child),
        (mother, PartyRelationType.PAYS_FOR, paid_for),
        # Incoming relations lend no reach.
        (stranger, PartyRelationType.PARENT_OF, mother),
    )

    access = Access.of(await resolve_reach(session, mother))

    assert access.basis(mother) is ReachBasis.SELF
    assert access.basis(child) is ReachBasis.GUARDIAN
    assert access.basis(paid_for) is ReachBasis.GUARDIAN
    assert access.basis(stranger) is None
    assert access.party_ids == {mother, child, paid_for}


async def test_a_party_without_relations_reaches_only_itself(session: AsyncSession):
    (alone,) = await _parties(session, 1)

    assert await resolve_reach(session, alone) == {alone: ReachBasis.SELF}


async def test_the_reach_is_the_set_the_bots_delegation_check_allows(session: AsyncSession):
    """One `DELEGATION_RELATION_TYPES` behind both: the bot's check and the API's reach cannot drift apart."""
    actor, child, paid_for, tutored, both, stranger = await _parties(session, 6)
    await _relate(
        session,
        (actor, PartyRelationType.PARENT_OF, child),
        (actor, PartyRelationType.PAYS_FOR, paid_for),
        (actor, PartyRelationType.TUTOR_OF, tutored),
        (actor, PartyRelationType.PARENT_OF, both),
        (actor, PartyRelationType.PAYS_FOR, both),
        (stranger, PartyRelationType.PARENT_OF, actor),
    )
    party = await session.scalar(select(Party).where(Party.id == actor).options(selectinload(Party.outgoing_relations)))

    reach = await resolve_reach(session, actor)

    assert set(reach) == _allowed_target_parties(party) == {actor, child, paid_for, both}
