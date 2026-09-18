import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Company, ContactInfo, Party, PartyType

from .errors import CompanyNotFoundError
from .inputs import NewContactInfo, normalize_contact_value
from .parties import load_party, saved


async def create_company(
    session: AsyncSession,
    *,
    name: str,
    contact_infos: Sequence[NewContactInfo] = (),
) -> Party:
    """Create a party of type company together with its contact infos."""
    party = Party(
        id=uuid.uuid4(),
        type=PartyType.COMPANY,
        company=Company(name=name),
        contact_infos=[
            ContactInfo(type=info.type, value=normalize_contact_value(info.type, info.value), label=info.label)
            for info in contact_infos
        ],
    )
    session.add(party)

    await saved(session, party.id)
    return await load_party(session, party.id)


async def get_company(session: AsyncSession, party_id: uuid.UUID) -> Company:
    """Return the company behind ``party_id``; a person's ID does not exist from here."""
    company = await session.get(Company, party_id)
    if company is None:
        raise CompanyNotFoundError(f"No company with party id {party_id}")

    return company


async def update_company(session: AsyncSession, party_id: uuid.UUID, *, name: str | None = None) -> Party:
    """Change the given fields; ``None`` means unchanged (the column is ``NOT NULL``).

    With nothing to change the aggregate is not written, so ``party.updated_at`` stays put.
    """
    company = await get_company(session, party_id)
    if name is None:
        return await load_party(session, party_id)

    company.name = name

    await saved(session, party_id)
    return await load_party(session, party_id)
