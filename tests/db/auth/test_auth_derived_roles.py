"""`student`, `tutor` and `guardian` follow from the CRM; only `admin` is stored (decision J)."""

import pytest

from app.core.auth.roles import STORED_ROLES, Role
from app.core.auth.services.roles import derive_roles, derive_roles_for
from app.core.db.models import (
    PartyRelation,
    PartyRelationType,
    PreferredMeetingTool,
    Student,
    Tutor,
    UserAccountRoleName,
)

pytestmark = pytest.mark.db


async def test_the_detail_lists_student_for_a_party_with_a_student_row(client, make_person, session):
    party = await make_person()
    session.add(Student(person_id=party.id, preferred_meeting_tool=PreferredMeetingTool.DISCORD))
    await session.flush()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    response = await client.get(f"/users/{user_id}")

    assert response.json()["roles"] == ["student"]


async def test_the_detail_lists_guardian_for_a_party_with_an_outgoing_pays_for(client, make_person, session):
    payer, child = await make_person("Petra"), await make_person("Kim")
    session.add(PartyRelation(from_party_id=payer.id, to_party_id=child.id, type=PartyRelationType.PAYS_FOR))
    await session.flush()
    user_id = (await client.post("/users", json={"party_id": str(payer.id), "email": "petra@example.org"})).json()["id"]

    response = await client.get(f"/users/{user_id}")

    assert response.json()["roles"] == ["guardian"]


async def test_the_detail_lists_admin_and_tutor_for_a_tutor_holding_the_stored_role(client, make_person, session):
    party = await make_person("Tim")
    session.add(Tutor(person_id=party.id))
    await session.flush()

    response = await client.post(
        "/users", json={"party_id": str(party.id), "email": "tim@example.org", "roles": ["admin"]}
    )

    assert response.json()["roles"] == ["admin", "tutor"]


async def test_an_incoming_relation_does_not_make_the_other_side_a_guardian(client, make_person, session):
    payer, child = await make_person("Petra"), await make_person("Kim")
    session.add(PartyRelation(from_party_id=payer.id, to_party_id=child.id, type=PartyRelationType.PAYS_FOR))
    await session.flush()

    assert await derive_roles(session, child.id) == frozenset()


async def test_tutor_of_does_not_make_a_tutor_a_guardian(client, make_person, session):
    tutor, student = await make_person("Tim"), await make_person("Kim")
    session.add(PartyRelation(from_party_id=tutor.id, to_party_id=student.id, type=PartyRelationType.TUTOR_OF))
    await session.flush()

    assert await derive_roles(session, tutor.id) == frozenset()


async def test_deriving_for_several_parties_answers_for_every_one_of_them(make_person, session):
    student, nobody = await make_person("Kim"), await make_person("Nemo")
    session.add(Student(person_id=student.id, preferred_meeting_tool=PreferredMeetingTool.DISCORD))
    await session.flush()

    derived = await derive_roles_for(session, [student.id, nobody.id])

    assert derived == {student.id: frozenset({Role.STUDENT}), nobody.id: frozenset()}


async def test_deriving_for_nothing_asks_the_database_nothing(session, statements):
    statements.clear()

    assert await derive_roles_for(session, []) == {}
    assert statements == []


def test_the_stored_role_column_lists_exactly_the_stored_roles():
    """The path parameter is validated by the column's enum; it must not drift from `STORED_ROLES`."""
    assert {Role(name.value) for name in UserAccountRoleName} == STORED_ROLES
