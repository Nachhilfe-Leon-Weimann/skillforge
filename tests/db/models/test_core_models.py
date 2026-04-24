import pytest


@pytest.mark.db
async def test_create_party(session):
    import uuid

    from skillcore.db.models import Party, PartyType

    party = Party(type=PartyType.PERSON)

    session.add(party)
    await session.flush()

    assert isinstance(party.id, uuid.UUID)


@pytest.mark.db
async def test_person_relationship(session):
    from skillcore.db.models import Party, PartyType, Person

    party = Party(type=PartyType.PERSON)
    person = Person(firstname="Max", lastname="Mustermann", party=party)

    session.add_all([party, person])
    await session.flush()

    assert person.party_id == party.id
    assert party.person is person


@pytest.mark.db
async def test_company_relationship(session):
    from skillcore.db.models import Company, Party, PartyType

    party = Party(type=PartyType.COMPANY)
    company = Company(name="xdreverGmbH", party=party)

    session.add_all([party, company])
    await session.flush()

    assert company.party_id == party.id
    assert party.company is company


@pytest.mark.db
async def test_cascade_delete(session):
    from skillcore.db.models import Party, PartyType, Person

    party = Party(type=PartyType.PERSON)
    person = Person(firstname="Max", lastname="Mustermann", party=party)

    session.add_all([party, person])
    await session.flush()

    await session.delete(party)
    await session.flush()

    result = await session.get(Person, party.id)

    assert result is None


@pytest.mark.db
async def test_party_relation_relationships(session):
    from skillcore.db.models import Party, PartyRelation, PartyRelationType, PartyType

    tutor_party = Party(type=PartyType.PERSON)
    student_party = Party(type=PartyType.PERSON)
    relation = PartyRelation(
        from_party=tutor_party,
        to_party=student_party,
        type=PartyRelationType.TUTOR_OF,
    )

    session.add_all([tutor_party, student_party, relation])
    await session.flush()

    assert relation.from_party_id == tutor_party.id
    assert relation.to_party_id == student_party.id
    assert tutor_party.outgoing_relations == [relation]
    assert student_party.incoming_relations == [relation]


@pytest.mark.db
async def test_student_tutor_subject_relationships(session):
    from skillcore.db.models import (
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

    student_party = Party(type=PartyType.PERSON)
    tutor_party = Party(type=PartyType.PERSON)
    student_person = Person(firstname="Ada", lastname="Student", party=student_party)
    tutor_person = Person(firstname="Linus", lastname="Tutor", party=tutor_party)
    student = Student(person=student_person, preferred_meeting_tool=PreferredMeetingTool.DISCORD)
    tutor = Tutor(person=tutor_person)
    subject = Subject(title="Mathematik")
    student_subject = StudentSubject(student=student, subject=subject)
    tutor_subject = TutorSubject(tutor=tutor, subject=subject)

    session.add_all([
        student_party,
        tutor_party,
        student_person,
        tutor_person,
        student,
        tutor,
        subject,
        student_subject,
        tutor_subject,
    ])
    await session.flush()

    assert student.person_id == student_person.party_id
    assert tutor.person_id == tutor_person.party_id
    assert student.student_subjects == [student_subject]
    assert tutor.tutor_subjects == [tutor_subject]
    assert subject.student_subjects == [student_subject]
    assert subject.tutor_subjects == [tutor_subject]
