"""`/api/v1/crm/subjects` against the real database."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import (
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

pytestmark = pytest.mark.db


async def _create(client: AsyncClient, title: str) -> dict:
    response = await client.post("/subjects", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


async def _titles(session: AsyncSession) -> list[str]:
    return sorted((await session.execute(select(Subject.title))).scalars())


async def test_create_subject_answers_201_with_the_stripped_title(client: AsyncClient):
    response = await client.post("/subjects", json={"title": "  Mathematics "})

    assert response.status_code == 201
    assert response.json() == {"id": response.json()["id"], "title": "Mathematics"}


@pytest.mark.parametrize("title", ["mathematics", "MATHEMATICS", "  Mathematics  ", " mAtHeMaTiCs"])
async def test_create_subject_conflicts_with_a_title_differing_only_in_case_or_whitespace(
    client: AsyncClient, session: AsyncSession, title: str
):
    await _create(client, "Mathematics")

    response = await client.post("/subjects", json={"title": title})

    assert response.status_code == 409
    assert response.json() == {"detail": "Subject already exists", "code": "subject_already_exists"}
    assert await _titles(session) == ["Mathematics"]


@pytest.mark.parametrize("title", ["mathematics", "  MATHEMATICS "])
async def test_update_subject_conflicts_with_a_title_differing_only_in_case_or_whitespace(
    client: AsyncClient, session: AsyncSession, title: str
):
    await _create(client, "Mathematics")
    physics = await _create(client, "Physics")

    response = await client.patch(f"/subjects/{physics['id']}", json={"title": title})

    assert response.status_code == 409
    assert response.json() == {"detail": "Subject already exists", "code": "subject_already_exists"}
    assert await _titles(session) == ["Mathematics", "Physics"]


async def test_update_subject_changes_the_title_and_may_recase_its_own(client: AsyncClient):
    subject = await _create(client, "Mathematics")

    renamed = await client.patch(f"/subjects/{subject['id']}", json={"title": " Maths "})
    recased = await client.patch(f"/subjects/{subject['id']}", json={"title": "MATHS"})

    assert (renamed.status_code, renamed.json()) == (200, {"id": subject["id"], "title": "Maths"})
    assert (recased.status_code, recased.json()) == (200, {"id": subject["id"], "title": "MATHS"})


async def test_update_subject_with_an_empty_body_changes_nothing(client: AsyncClient, session: AsyncSession):
    subject = await _create(client, "Mathematics")

    response = await client.patch(f"/subjects/{subject['id']}", json={})

    assert response.status_code == 200
    assert response.json() == subject
    assert await _titles(session) == ["Mathematics"]


@pytest.mark.parametrize("title", [None, "", "   "])
async def test_update_subject_rejects_a_null_or_blank_title(client: AsyncClient, session: AsyncSession, title):
    subject = await _create(client, "Mathematics")

    response = await client.patch(f"/subjects/{subject['id']}", json={"title": title})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    # The `Name | MISSING` union reports one error per member, all under the same field.
    assert {tuple(error["loc"][:2]) for error in body["errors"]} == {("body", "title")}
    assert await _titles(session) == ["Mathematics"]


async def test_update_and_delete_of_an_unknown_subject_are_404(client: AsyncClient):
    expected = {"detail": "Subject not found", "code": "subject_not_found"}

    patched = await client.patch("/subjects/987654", json={"title": "Chemistry"})
    deleted = await client.delete("/subjects/987654")

    assert (patched.status_code, patched.json()) == (404, expected)
    assert (deleted.status_code, deleted.json()) == (404, expected)


async def test_a_subject_id_outside_the_integer_range_is_a_validation_error(client: AsyncClient):
    response = await client.delete(f"/subjects/{2**31}")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_delete_subject_answers_204_and_removes_it(client: AsyncClient, session: AsyncSession):
    subject = await _create(client, "Mathematics")

    response = await client.delete(f"/subjects/{subject['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert await _titles(session) == []


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_delete_subject_is_409_while_a_role_references_it(client: AsyncClient, session: AsyncSession, role: str):
    subject = await _create(client, "Mathematics")
    person = Person(firstname="Max", lastname="Mustermann")
    if role == "student":
        person.student = Student(
            preferred_meeting_tool=PreferredMeetingTool.DISCORD,
            student_subjects=[StudentSubject(subject_id=subject["id"])],
        )
    else:
        person.tutor = Tutor(tutor_subjects=[TutorSubject(subject_id=subject["id"])])
    session.add(Party(type=PartyType.PERSON, person=person))
    await session.flush()

    response = await client.delete(f"/subjects/{subject['id']}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Subject is still assigned to students or tutors",
        "code": "subject_in_use",
    }
    assert await _titles(session) == ["Mathematics"]


async def test_list_subjects_is_a_page_ordered_by_lowercased_title_then_id(client: AsyncClient):
    # "art" sorts after "Biology" by code point, so on a C-collated database only lower() orders them.
    created = [await _create(client, title) for title in ["physics", "Biology", "chemistry", "art"]]
    by_title = {subject["title"]: subject for subject in created}

    full = await client.get("/subjects")
    second_page = await client.get("/subjects", params={"limit": 2, "offset": 2})

    assert full.status_code == 200
    assert full.json() == {
        "items": [by_title["art"], by_title["Biology"], by_title["chemistry"], by_title["physics"]],
        "total": 4,
        "limit": 50,
        "offset": 0,
    }
    assert second_page.json() == {
        "items": [by_title["chemistry"], by_title["physics"]],
        "total": 4,
        "limit": 2,
        "offset": 2,
    }


async def test_list_subjects_orders_in_sql_by_lowercased_title_then_id(client: AsyncClient, statements: list[str]):
    """The test database collates case-insensitively anyway, so the data alone cannot prove ``lower()``."""
    await _create(client, "Mathematics")
    statements.clear()

    await client.get("/subjects")

    listing = [statement for statement in statements if "FROM core.subject ORDER BY" in statement]
    assert len(listing) == 1, statements
    assert "ORDER BY lower(core.subject.title), core.subject.id LIMIT" in listing[0]


async def test_list_subjects_rejects_an_unknown_query_parameter(client: AsyncClient):
    response = await client.get("/subjects", params={"limt": 5})

    assert response.status_code == 422
    assert [error["loc"] for error in response.json()["errors"]] == [["query", "limt"]]


async def test_a_title_conflict_leaves_the_surrounding_transaction_usable(session: AsyncSession):
    """The violation happens inside a SAVEPOINT - not in a flush that precedes it."""
    from app.services.crm import subjects
    from app.services.crm.errors import SubjectAlreadyExistsError

    mathematics = await subjects.create_subject(session, title="Mathematics")
    physics = await subjects.create_subject(session, title="Physics")
    physics_id = physics.id

    with pytest.raises(SubjectAlreadyExistsError):
        await subjects.create_subject(session, title="MATHEMATICS")
    with pytest.raises(SubjectAlreadyExistsError):
        await subjects.update_subject(session, physics_id, title="mathematics")

    # Without a real SAVEPOINT the session would now answer PendingRollbackError.
    chemistry = await subjects.create_subject(session, title="Chemistry")
    assert chemistry.id not in {mathematics.id, physics_id}
    assert await _titles(session) == ["Chemistry", "Mathematics", "Physics"]
    # The rollback expired what the failed update touched; the row itself kept its title.
    assert await session.scalar(select(Subject.title).where(Subject.id == physics_id)) == "Physics"
