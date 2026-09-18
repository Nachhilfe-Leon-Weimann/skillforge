import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ContactInfo, Party, PartyType, Person

from .errors import PersonNotFoundError
from .inputs import NewContactInfo, normalize_contact_value
from .parties import load_party, saved


async def create_person(
    session: AsyncSession,
    *,
    firstname: str,
    lastname: str,
    contact_infos: Sequence[NewContactInfo] = (),
) -> Party:
    """Create a party of type person together with its contact infos."""
    party = Party(
        id=uuid.uuid4(),
        type=PartyType.PERSON,
        person=Person(firstname=firstname, lastname=lastname),
        contact_infos=[
            ContactInfo(type=info.type, value=normalize_contact_value(info.type, info.value), label=info.label)
            for info in contact_infos
        ],
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
