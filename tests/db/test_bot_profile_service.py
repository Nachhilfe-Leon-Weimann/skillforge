"""The party graph the bot loads for a Discord ID, and what the operational profile shows of it."""

import warnings

import pytest
from sqlalchemy.exc import SAWarning

from app.api.v1.bot.schemas import OperationalProfile
from app.core.db.models import (
    Company,
    DiscordAccount,
    Party,
    PartyType,
    Person,
    PreferredMeetingTool,
    Student,
    StudentSubject,
    Subject,
    Tutor,
    TutorSubject,
)
from app.services.bot.profile import load_parties_for_discord_ids

PRIMARY_ID = 900000000000000002
SECONDARY_ID = 900000000000000001


@pytest.mark.db
async def test_profile_lists_every_discord_account_of_the_party_primary_first(session):
    party = Party(type=PartyType.PERSON)
    session.add_all([
        party,
        Person(firstname="Max", lastname="Muster", party=party),
        DiscordAccount(discord_id=SECONDARY_ID, party=party, is_primary=False),
        DiscordAccount(discord_id=PRIMARY_ID, party=party, is_primary=True),
    ])
    await session.flush()
    # The loader must fill the collection, not the identity map that still knows the insert order.
    session.expunge_all()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        parties = await load_parties_for_discord_ids(session, [SECONDARY_ID])

    profile = OperationalProfile.from_party(parties[SECONDARY_ID])
    assert [(account.discord_id, account.is_primary) for account in profile.external_accounts.discord] == [
        (PRIMARY_ID, True),
        (SECONDARY_ID, False),
    ]


@pytest.mark.db
async def test_profile_maps_for_a_person_holding_both_roles_and_for_a_company(session):
    """Async sessions cannot lazy-load: everything ``from_party`` touches must come from the loader."""
    person_party = Party(type=PartyType.PERSON)
    person = Person(firstname="Erika", lastname="Beispiel", party=person_party)
    subject = Subject(title="Profile graph subject")
    student = Student(preferred_meeting_tool=PreferredMeetingTool.DISCORD, person=person)
    tutor = Tutor(person=person)
    company_party = Party(type=PartyType.COMPANY)
    session.add_all([
        person_party,
        person,
        subject,
        student,
        tutor,
        StudentSubject(student=student, subject=subject),
        TutorSubject(tutor=tutor, subject=subject),
        company_party,
        Company(name="Beispiel GmbH", party=company_party),
        DiscordAccount(discord_id=PRIMARY_ID, party=person_party, is_primary=True),
        DiscordAccount(discord_id=SECONDARY_ID, party=company_party, is_primary=True),
    ])
    await session.flush()
    session.expunge_all()

    parties = await load_parties_for_discord_ids(session, [PRIMARY_ID, SECONDARY_ID])

    person_profile = OperationalProfile.from_party(parties[PRIMARY_ID])
    assert person_profile.subjects == ["Profile graph subject"]
    company_profile = OperationalProfile.from_party(parties[SECONDARY_ID])
    assert company_profile.person is None
    assert [account.discord_id for account in company_profile.external_accounts.discord] == [SECONDARY_ID]


@pytest.mark.db
async def test_the_loader_is_built_on_the_party_graph_of_the_crm(session, statements):
    """``core.company`` is loaded by ``PARTY_GRAPH`` only: the bot's profile never touches it."""
    party = Party(type=PartyType.COMPANY)
    session.add_all([
        party,
        Company(name="Beispiel GmbH", party=party),
        DiscordAccount(discord_id=PRIMARY_ID, party=party, is_primary=True),
    ])
    await session.flush()
    session.expunge_all()
    statements.clear()

    parties = await load_parties_for_discord_ids(session, [PRIMARY_ID])

    assert any("FROM core.company" in statement for statement in statements)
    company = parties[PRIMARY_ID].company
    assert company is not None
    assert company.name == "Beispiel GmbH"
