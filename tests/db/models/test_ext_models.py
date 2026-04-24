import pytest


@pytest.mark.db
async def test_ext_relationships(session):
    from skillcore.db.models import DiscordAccount, Party, PartyType

    party = Party(type=PartyType.PERSON)
    discord_account = DiscordAccount(discord_id="123456789", party=party)

    session.add_all([party, discord_account])
    await session.flush()

    assert discord_account.party_id == party.id
    assert party.discord_account is discord_account


@pytest.mark.db
async def test_tutor_ms_account_relationship(session):
    from skillcore.db.models import MicrosoftAccount, Party, PartyType, Person, Tutor

    def build_tutor() -> Tutor:
        party = Party(type=PartyType.PERSON)
        person = Person(firstname="Max", lastname="Mustermann", party=party)
        session.add_all([party, person])
        return Tutor(person=person)

    tutor = build_tutor()
    ms_account = MicrosoftAccount(user_id="SOME_MS_GRAPH_USER_ID", party=tutor.person.party)

    session.add_all([tutor, ms_account])
    await session.flush()

    assert ms_account.party_id == tutor.person.party_id
    assert tutor.person.party.microsoft_account is ms_account


@pytest.mark.db
async def test_student_ms_contact_relationship(session):
    from skillcore.db.models import MicrosoftContact, Party, PartyType, Person, PreferredMeetingTool, Student

    def build_student() -> Student:
        party = Party(type=PartyType.PERSON)
        person = Person(firstname="Max", lastname="Mustermann", party=party)
        session.add_all([party, person])
        return Student(preferred_meeting_tool=PreferredMeetingTool.MICROSOFT_TEAMS, person=person)

    student = build_student()
    ms_contact = MicrosoftContact(contact_id="SOME_MS_GRAPH_CONTACT_ID", party=student.person.party)

    session.add_all([student, ms_contact])
    await session.flush()

    assert ms_contact.party_id == student.person.party_id
    assert student.person.party.microsoft_contact is ms_contact
