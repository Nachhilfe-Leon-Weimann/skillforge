"""The two rules every CRM write service follows (decisions H and M of the CRM API spec).

- **Reload rule:** the result can be mapped with ``party_detail()`` without a lazy load, which async
  SQLAlchemy would answer with ``MissingGreenlet``.
- **Aggregate root:** the write moves ``party.updated_at`` of every party it touches.

``WRITES`` holds one scenario per write service function; ``test_every_write_service_has_a_scenario``
fails when a slice adds a function without adding it here.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import FunctionType
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.crm.schemas import party_detail
from app.core.db.models import ContactInfoType, Party
from app.services.crm import companies, parties, persons
from app.services.crm.inputs import NewContactInfo

pytestmark = pytest.mark.db

WRITE_MODULES = (persons, companies)
# Public functions of the write modules that do not write.
READS = {persons.get_person, companies.get_company}


@dataclass(frozen=True)
class Seed:
    """What exists before a write runs; every party's ``updated_at`` lies in the past."""

    person_id: uuid.UUID
    company_id: uuid.UUID


@dataclass(frozen=True)
class Write:
    service: FunctionType
    """The service function the scenario exercises; the completeness check compares these."""
    run: Callable[[AsyncSession, Seed], Awaitable[Any]]
    touches: Callable[[Seed], tuple[uuid.UUID, ...]] = lambda seed: ()
    """The existing parties whose aggregate the write changes."""
    creates: bool = False
    """The write creates its party, so there is no earlier ``updated_at`` to move."""
    label: str = ""

    @property
    def id(self) -> str:
        return f"{self.service.__module__.rsplit('.', 1)[-1]}.{self.service.__name__}{self.label}"


CONTACT_INFOS = [
    NewContactInfo(ContactInfoType.EMAIL, "Max.Mustermann@Example.com", "private"),
    NewContactInfo(ContactInfoType.PHONE, "0151 234 567"),
]

WRITES = [
    Write(
        persons.create_person,
        lambda session, seed: persons.create_person(session, firstname="Erika", lastname="Musterfrau"),
        creates=True,
        label="[without roles or contact infos]",
    ),
    Write(
        persons.create_person,
        lambda session, seed: persons.create_person(
            session, firstname="Erika", lastname="Musterfrau", contact_infos=CONTACT_INFOS
        ),
        creates=True,
        label="[with contact infos]",
    ),
    Write(
        persons.update_person,
        lambda session, seed: persons.update_person(session, seed.person_id, firstname="Maximilian"),
        touches=lambda seed: (seed.person_id,),
    ),
    Write(
        companies.create_company,
        lambda session, seed: companies.create_company(session, name="Musterfirma GmbH", contact_infos=CONTACT_INFOS),
        creates=True,
    ),
    Write(
        companies.update_company,
        lambda session, seed: companies.update_company(session, seed.company_id, name="Musterfirma AG"),
        touches=lambda seed: (seed.company_id,),
    ),
]


@pytest.fixture
async def seed(session: AsyncSession, backdate) -> Seed:
    person = await persons.create_person(session, firstname="Max", lastname="Mustermann", contact_infos=CONTACT_INFOS)
    company = await companies.create_company(session, name="Musterfirma GmbH")
    await backdate(person.id, company.id)
    return Seed(person_id=person.id, company_id=company.id)


@pytest.mark.parametrize("write", WRITES, ids=lambda write: write.id)
async def test_write_result_maps_to_the_detail_without_a_lazy_load(write: Write, session: AsyncSession, seed: Seed):
    result = await write.run(session, seed)

    if isinstance(result, Party):
        detail = party_detail(result)
        assert detail.id == result.id
        assert detail.updated_at == result.updated_at
    for party_id in write.touches(seed):
        assert party_detail(await parties.load_party(session, party_id)).id == party_id


@pytest.mark.parametrize("write", [write for write in WRITES if not write.creates], ids=lambda write: write.id)
async def test_write_moves_updated_at_of_every_party_it_touches(
    write: Write, session: AsyncSession, seed: Seed, updated_at
):
    touched = write.touches(seed)
    assert touched, "a write on an existing aggregate names the parties it touches"
    before = {party_id: await updated_at(party_id) for party_id in touched}

    result = await write.run(session, seed)

    for party_id in touched:
        assert await updated_at(party_id) > before[party_id]
    if isinstance(result, Party):
        assert party_detail(result).updated_at == await updated_at(result.id)


async def test_a_write_leaves_the_other_parties_alone(session: AsyncSession, seed: Seed, updated_at):
    before = await updated_at(seed.company_id)

    await persons.update_person(session, seed.person_id, firstname="Maximilian")

    assert await updated_at(seed.company_id) == before


@pytest.mark.parametrize("target", ["person", "company"])
async def test_an_update_with_nothing_to_change_is_not_a_write(
    target: str, session: AsyncSession, seed: Seed, updated_at
):
    party_id = seed.person_id if target == "person" else seed.company_id
    before = await updated_at(party_id)

    if target == "person":
        party = await persons.update_person(session, party_id)
    else:
        party = await companies.update_company(session, party_id)

    assert await updated_at(party_id) == before
    assert party_detail(party).updated_at == before


def test_every_write_service_has_a_scenario():
    public = {
        function
        for module in WRITE_MODULES
        for name, function in vars(module).items()
        if inspect.iscoroutinefunction(function) and not name.startswith("_") and function.__module__ == module.__name__
    }

    assert public - READS == {write.service for write in WRITES}
