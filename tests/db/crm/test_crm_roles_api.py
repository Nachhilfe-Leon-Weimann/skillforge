"""The student and tutor roles - `PUT` / `DELETE /persons/{party_id}/student|tutor` and the nested create."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Party, PartyRelation, PartyRelationType, Student, StudentSubject, Tutor, TutorSubject

pytestmark = pytest.mark.db


@pytest.fixture
async def subjects(client: AsyncClient) -> dict[str, int]:
    created = {}
    for title in ("Mathematics", "physics", "Art"):
        response = await client.post("/subjects", json={"title": title})
        assert response.status_code == 201
        created[title] = response.json()["id"]
    return created


async def _person(client: AsyncClient, **body) -> dict:
    response = await client.post("/persons", json={"firstname": "Max", "lastname": "Mustermann", **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _company(client: AsyncClient) -> dict:
    response = await client.post("/companies", json={"name": "Musterfirma GmbH"})
    assert response.status_code == 201
    return response.json()


async def _count(session: AsyncSession, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _titles(role: dict) -> list[str]:
    return [subject["title"] for subject in role["subjects"]]


# --- PUT ---


async def test_put_student_role_creates_the_role_and_answers_with_the_person_detail(
    client: AsyncClient, subjects: dict[str, int]
):
    person = await _person(client)

    response = await client.put(
        f"/persons/{person['id']}/student",
        json={"preferred_meeting_tool": "microsoft_teams", "subject_ids": [subjects["physics"], subjects["Art"]]},
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail == {
        **person,
        "student": {
            "preferred_meeting_tool": "microsoft_teams",
            "subjects": [{"id": subjects["Art"], "title": "Art"}, {"id": subjects["physics"], "title": "physics"}],
        },
        "updated_at": detail["updated_at"],
    }
    assert (await client.get(f"/parties/{person['id']}")).json() == detail


async def test_put_tutor_role_creates_the_role_and_a_person_can_hold_both(
    client: AsyncClient, subjects: dict[str, int]
):
    person = await _person(client)
    await client.put(f"/persons/{person['id']}/student", json={"preferred_meeting_tool": "discord"})

    response = await client.put(f"/persons/{person['id']}/tutor", json={"subject_ids": [subjects["Mathematics"]]})

    assert response.status_code == 200
    detail = response.json()
    assert detail["student"] == {"preferred_meeting_tool": "discord", "subjects": []}
    assert detail["tutor"] == {"subjects": [{"id": subjects["Mathematics"], "title": "Mathematics"}]}
    listed = (await client.get("/parties", params={"role": "tutor"})).json()["items"]
    assert [(item["id"], item["roles"]) for item in listed] == [(person["id"], ["student", "tutor"])]


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_the_same_put_twice_answers_200_twice_with_the_same_representation(
    client: AsyncClient, subjects: dict[str, int], backdate, updated_at, role: str
):
    person = await _person(client)
    body = {"subject_ids": [subjects["Mathematics"], subjects["Art"]]}
    if role == "student":
        body["preferred_meeting_tool"] = "in_person"

    first = await client.put(f"/persons/{person['id']}/{role}", json=body)
    # In production the second request is a later transaction: only a timestamp in the past shows
    # whether it counted as a write.
    await backdate(uuid.UUID(person["id"]))
    before = await updated_at(uuid.UUID(person["id"]))
    second = await client.put(f"/persons/{person['id']}/{role}", json=body)

    assert (first.status_code, second.status_code) == (200, 200)
    assert await updated_at(uuid.UUID(person["id"])) == before
    assert {**second.json(), "updated_at": None} == {**first.json(), "updated_at": None}
    assert second.json() == (await client.get(f"/parties/{person['id']}")).json()


async def test_put_replaces_the_data_of_an_existing_role(client: AsyncClient, subjects: dict[str, int]):
    person = await _person(client)
    url = f"/persons/{person['id']}/student"
    await client.put(url, json={"preferred_meeting_tool": "discord", "subject_ids": [subjects["Art"]]})

    tool_only = await client.put(url, json={"preferred_meeting_tool": "phone", "subject_ids": [subjects["Art"]]})
    emptied = await client.put(url, json={"preferred_meeting_tool": "phone"})

    assert tool_only.json()["student"] == {
        "preferred_meeting_tool": "phone",
        "subjects": [{"id": subjects["Art"], "title": "Art"}],
    }
    assert emptied.json()["student"] == {"preferred_meeting_tool": "phone", "subjects": []}


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_subject_ids_replaces_the_set_by_its_difference(
    client: AsyncClient, session: AsyncSession, subjects: dict[str, int], statements: list[str], role: str
):
    person = await _person(client)
    url = f"/persons/{person['id']}/{role}"
    tool = {"preferred_meeting_tool": "discord"} if role == "student" else {}
    await client.put(url, json={**tool, "subject_ids": [subjects["Mathematics"], subjects["physics"]]})
    session.expunge_all()
    statements.clear()

    response = await client.put(url, json={**tool, "subject_ids": [subjects["physics"], subjects["Art"]]})

    assert _titles(response.json()[role]) == ["Art", "physics"]
    # The unchanged row (physics) is neither deleted nor re-inserted.
    table = f"core.{role}_subject"
    assert len([statement for statement in statements if statement.startswith(f"DELETE FROM {table}")]) == 1
    assert len([statement for statement in statements if statement.startswith(f"INSERT INTO {table}")]) == 1
    link = StudentSubject if role == "student" else TutorSubject
    stored = (await session.execute(select(link.subject_id))).scalars().all()
    assert sorted(stored) == sorted([subjects["physics"], subjects["Art"]])


async def test_duplicate_subject_ids_collapse(client: AsyncClient, subjects: dict[str, int]):
    person = await _person(client)
    maths = subjects["Mathematics"]

    response = await client.put(f"/persons/{person['id']}/tutor", json={"subject_ids": [maths, maths, maths]})

    assert response.status_code == 200
    assert _titles(response.json()["tutor"]) == ["Mathematics"]


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_an_unknown_subject_is_422_listing_the_unknown_ids_and_nothing_is_written(
    client: AsyncClient, session: AsyncSession, subjects: dict[str, int], backdate, updated_at, role: str
):
    person = await _person(client)
    url = f"/persons/{person['id']}/{role}"
    tool = {"preferred_meeting_tool": "discord"} if role == "student" else {}
    await client.put(url, json={**tool, "subject_ids": [subjects["Art"]]})
    await backdate(uuid.UUID(person["id"]))
    before = (await client.get(f"/parties/{person['id']}")).json()
    unknown = [max(subjects.values()) + 20, max(subjects.values()) + 10]

    response = await client.put(
        url, json={"preferred_meeting_tool": "phone", "subject_ids": [unknown[0], subjects["physics"], unknown[1]]}
    )

    assert response.status_code == 422
    assert response.json() == {"detail": f"Unknown subject: {unknown[1]}, {unknown[0]}", "code": "unknown_subject"}
    assert (await client.get(f"/parties/{person['id']}")).json() == before


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_an_unknown_subject_on_a_person_without_the_role_creates_no_role(
    client: AsyncClient, session: AsyncSession, role: str
):
    person = await _person(client)

    response = await client.put(
        f"/persons/{person['id']}/{role}", json={"preferred_meeting_tool": "discord", "subject_ids": [424242]}
    )

    assert (response.status_code, response.json()["code"]) == (422, "unknown_subject")
    assert await _count(session, Student) == 0
    assert await _count(session, Tutor) == 0


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        ({"subject_ids": [0]}, ["body", "subject_ids", 0]),
        ({"subject_ids": [2**31]}, ["body", "subject_ids", 0]),
        ({"subject_ids": ["maths"]}, ["body", "subject_ids", 0]),
        ({"subject_ids": None}, ["body", "subject_ids"]),
    ],
    ids=["zero", "beyond the integer range", "not a number", "null"],
)
async def test_malformed_subject_ids_are_the_validation_422(client: AsyncClient, body: dict, loc: list):
    person = await _person(client)

    response = await client.put(f"/persons/{person['id']}/tutor", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert [error["loc"] for error in response.json()["errors"]] == [loc]


async def test_put_student_role_requires_the_meeting_tool(client: AsyncClient):
    person = await _person(client)

    missing = await client.put(f"/persons/{person['id']}/student", json={})
    unknown = await client.put(f"/persons/{person['id']}/student", json={"preferred_meeting_tool": "carrier_pigeon"})

    assert [error["loc"] for error in missing.json()["errors"]] == [["body", "preferred_meeting_tool"]]
    assert [error["loc"] for error in unknown.json()["errors"]] == [["body", "preferred_meeting_tool"]]


# --- DELETE ---


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_delete_removes_the_role_and_only_that_role(client: AsyncClient, subjects: dict[str, int], role: str):
    person = await _person(
        client,
        student={"preferred_meeting_tool": "discord", "subject_ids": [subjects["Art"]]},
        tutor={"subject_ids": [subjects["physics"]]},
    )
    other = "tutor" if role == "student" else "student"

    response = await client.delete(f"/persons/{person['id']}/{role}")

    assert response.status_code == 204
    assert response.content == b""
    detail = (await client.get(f"/parties/{person['id']}")).json()
    assert detail[role] is None
    assert detail[other] == person[other]


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_delete_of_a_role_that_is_not_assigned_is_404_role_not_found(client: AsyncClient, role: str):
    person = await _person(client)
    other = "tutor" if role == "student" else "student"
    await client.put(f"/persons/{person['id']}/{other}", json={"preferred_meeting_tool": "discord"})

    first = await client.delete(f"/persons/{person['id']}/{role}")

    assert first.status_code == 404
    assert first.json() == {"detail": "Role not assigned", "code": "role_not_found"}
    assert (await client.delete(f"/persons/{person['id']}/{other}")).status_code == 204
    assert (await client.delete(f"/persons/{person['id']}/{other}")).json()["code"] == "role_not_found"


@pytest.mark.parametrize("role", ["student", "tutor"])
@pytest.mark.parametrize("target", ["company", "unknown"])
async def test_a_company_or_unknown_id_is_404_person_not_found(client: AsyncClient, role: str, target: str):
    party_id = (await _company(client))["id"] if target == "company" else str(uuid.uuid4())
    expected = {"detail": "Person not found", "code": "person_not_found"}

    put = await client.put(f"/persons/{party_id}/{role}", json={"preferred_meeting_tool": "discord"})
    delete = await client.delete(f"/persons/{party_id}/{role}")

    assert (put.status_code, put.json()) == (404, expected)
    assert (delete.status_code, delete.json()) == (404, expected)


@pytest.mark.parametrize("role", ["student", "tutor"])
async def test_removing_a_role_leaves_relations_alone_and_never_looks_at_bot_state(
    client: AsyncClient, session: AsyncSession, statements: list[str], role: str
):
    """ADR 0007: a CRM write is validated against CRM rules only."""
    tutor = await _person(client, tutor={})
    student = await _person(client, firstname="Mia", student={"preferred_meeting_tool": "discord"})
    session.add(
        PartyRelation(
            from_party_id=uuid.UUID(tutor["id"]), to_party_id=uuid.UUID(student["id"]), type=PartyRelationType.TUTOR_OF
        )
    )
    await session.flush()
    session.expunge_all()
    statements.clear()

    response = await client.delete(f"/persons/{(student if role == 'student' else tutor)['id']}/{role}")
    during_the_request = list(statements)  # the checks below query the same connection

    assert response.status_code == 204
    assert during_the_request, "the recorder saw the request"
    assert [statement for statement in during_the_request if " bot." in statement or " ext." in statement] == []
    assert [statement for statement in during_the_request if "party_relation" in statement] == []
    assert await _count(session, PartyRelation) == 1


# --- nested create ---


async def test_post_persons_creates_both_roles_in_the_same_call(client: AsyncClient, subjects: dict[str, int]):
    response = await client.post(
        "/persons",
        json={
            "firstname": "Max",
            "lastname": "Mustermann",
            "student": {"preferred_meeting_tool": "in_person", "subject_ids": [subjects["physics"], subjects["Art"]]},
            "tutor": {"subject_ids": [subjects["Mathematics"]]},
        },
    )

    assert response.status_code == 201
    detail = response.json()
    assert detail["student"]["preferred_meeting_tool"] == "in_person"
    assert _titles(detail["student"]) == ["Art", "physics"]
    assert _titles(detail["tutor"]) == ["Mathematics"]
    assert (await client.get(response.headers["location"].replace("/api/v1/crm", ""))).json() == detail


async def test_post_persons_with_a_null_or_absent_role_creates_none(client: AsyncClient):
    explicit = await _person(client, student=None, tutor=None)
    absent = await _person(client)

    assert (explicit["student"], explicit["tutor"], absent["student"], absent["tutor"]) == (None, None, None, None)


async def test_post_persons_with_an_unknown_subject_creates_no_party_at_all(
    client: AsyncClient, session: AsyncSession, subjects: dict[str, int]
):
    response = await client.post(
        "/persons",
        json={
            "firstname": "Max",
            "lastname": "Mustermann",
            "contact_infos": [{"type": "email", "value": "max@example.com"}],
            "student": {"preferred_meeting_tool": "discord", "subject_ids": [subjects["Art"], 900002]},
            "tutor": {"subject_ids": [900001, subjects["physics"]]},
        },
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown subject: 900001, 900002", "code": "unknown_subject"}
    assert await _count(session, Party) == 0
    assert await _count(session, Student) == 0
    assert await _count(session, Tutor) == 0
