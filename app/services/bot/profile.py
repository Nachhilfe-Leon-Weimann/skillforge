from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import (
    DiscordAccount,
    Party,
    Person,
    Student,
    StudentSubject,
    Tutor,
    TutorSubject,
)


async def load_party_for_discord_id(session: AsyncSession, discord_id: int) -> Party | None:
    """Load the party linked to a discord_id with the full operational profile graph eager-loaded.

    Everything ``OperationalProfile.from_party`` touches must be loaded here: async sessions
    cannot lazy-load, so a missing relation would raise at serialization time.
    """

    result = await session.execute(
        select(Party)
        .join(DiscordAccount, DiscordAccount.party_id == Party.id)
        .where(DiscordAccount.discord_id == discord_id)
        .options(
            selectinload(Party.person).options(
                selectinload(Person.tutor).selectinload(Tutor.tutor_subjects).selectinload(TutorSubject.subject),
                selectinload(Person.student)
                .selectinload(Student.student_subjects)
                .selectinload(StudentSubject.subject),
            ),
            selectinload(Party.contact_infos),
            selectinload(Party.outgoing_relations),
            selectinload(Party.incoming_relations),
            selectinload(Party.discord_account),
            selectinload(Party.microsoft_account),
        )
    )
    return result.scalar_one_or_none()
