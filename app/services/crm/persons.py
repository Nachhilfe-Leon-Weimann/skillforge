import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Party, PartyType, Person

from .contact_infos import new_contact_infos
from .errors import PersonNotFoundError
from .inputs import NewContactInfo, StudentRoleData, TutorRoleData
from .parties import load_party, saved
from .roles import apply_student_role, apply_tutor_role
from .subjects import require_subjects


async def create_person(
    session: AsyncSession,
    *,
    firstname: str,
    lastname: str,
    contact_infos: Sequence[NewContactInfo] = (),
    student: StudentRoleData | None = None,
    tutor: TutorRoleData | None = None,
) -> Party:
    """Create a party of type person together with its contact infos and roles."""
    # Checked before anything is added: an unknown subject creates no party at all.
    await require_subjects(
        session,
        (student.subject_ids if student else frozenset()) | (tutor.subject_ids if tutor else frozenset()),
    )

    person = Person(firstname=firstname, lastname=lastname)
    if student is not None:
        apply_student_role(
            person, preferred_meeting_tool=student.preferred_meeting_tool, subject_ids=student.subject_ids
        )
    if tutor is not None:
        apply_tutor_role(person, subject_ids=tutor.subject_ids)
    party = Party(
        id=uuid.uuid4(),
        type=PartyType.PERSON,
        person=person,
        contact_infos=new_contact_infos(contact_infos),
    )
    session.add(party)

    await saved(session, party.id)
    return await load_party(session, party.id)


async def get_person(session: AsyncSession, party_id: uuid.UUID) -> Person:
    """Return the person behind ``party_id``; a company's ID does not exist from here."""
    person = await session.get(Person, party_id)
    if person is None:
        raise PersonNotFoundError(f"No person with party id {party_id}")

    return person


async def update_person(
    session: AsyncSession,
    party_id: uuid.UUID,
    *,
    firstname: str | None = None,
    lastname: str | None = None,
) -> Party:
    """Change the given fields; ``None`` means unchanged (both columns are ``NOT NULL``).

    With nothing to change the aggregate is not written, so ``party.updated_at`` stays put.
    """
    person = await get_person(session, party_id)
    if firstname is None and lastname is None:
        return await load_party(session, party_id)

    if firstname is not None:
        person.firstname = firstname
    if lastname is not None:
        person.lastname = lastname

    await saved(session, party_id)
    return await load_party(session, party_id)
