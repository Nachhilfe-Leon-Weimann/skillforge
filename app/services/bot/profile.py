from collections.abc import Iterable

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
    parties = await load_parties_for_discord_ids(session, [discord_id])
    return parties.get(discord_id)


async def load_parties_for_discord_ids(session: AsyncSession, discord_ids: Iterable[int]) -> dict[int, Party]:
    """Load the parties linked to ``discord_ids`` with the full operational profile graph eager-loaded.

    Returns a party per id that has a linked account; ids without one are simply absent. Everything
    ``OperationalProfile.from_party`` touches must be loaded here: async sessions cannot lazy-load, so a
    missing relation would raise at serialization time.
    """

    ids = list(dict.fromkeys(discord_ids))
    if not ids:
        return {}

    result = await session.execute(
        select(DiscordAccount.discord_id, Party)
        .join(DiscordAccount, DiscordAccount.party_id == Party.id)
        .where(DiscordAccount.discord_id.in_(ids))
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
    return {discord_id: party for discord_id, party in result.all()}
