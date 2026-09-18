"""The party aggregate: its one loading path and the bookkeeping every write ends with."""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import Party, Person, Student, StudentSubject, Tutor, TutorSubject

from .errors import PartyNotFoundError

# Everything a party representation may touch. Async SQLAlchemy cannot lazy-load, so a mapper that
# needs more extends this tuple instead of loading on its own.
PARTY_GRAPH = (
    selectinload(Party.person)
    .selectinload(Person.student)
    .selectinload(Student.student_subjects)
    .selectinload(StudentSubject.subject),
    selectinload(Party.person)
    .selectinload(Person.tutor)
    .selectinload(Tutor.tutor_subjects)
    .selectinload(TutorSubject.subject),
    selectinload(Party.company),
    selectinload(Party.contact_infos),
)


async def load_party(session: AsyncSession, party_id: uuid.UUID) -> Party:
    """Load the party with its whole graph, refreshing whatever the session already holds."""
    result = await session.execute(
        select(Party).where(Party.id == party_id).options(*PARTY_GRAPH).execution_options(populate_existing=True)
    )
    party = result.scalar_one_or_none()
    if party is None:
        raise PartyNotFoundError(f"No party with id {party_id}")

    return party


async def saved(session: AsyncSession, *party_ids: uuid.UUID) -> None:
    """Mark the aggregates as changed and write everything that is pending.

    ``Party`` is the aggregate root: its ``updated_at`` is the one change signal consumers get, so
    every write inside the aggregate ends here. A party created in the same unit of work is not in
    the database yet; its ``updated_at`` comes from the column default on the flush below.
    """
    await session.execute(
        update(Party)
        .where(Party.id.in_(party_ids))
        .values(updated_at=func.now())
        # load_party() refreshes the instances afterwards, so nothing needs to be synchronized here.
        .execution_options(synchronize_session=False)
    )
    await session.flush()
