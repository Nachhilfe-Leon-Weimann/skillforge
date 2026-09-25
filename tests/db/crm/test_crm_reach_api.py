"""P0-7 of the user-authentication spec: the two party reads honor a person's reach, against the real database."""

import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Scope
from app.core.db.dependencies import get_db_session
from app.main import app

pytestmark = pytest.mark.db


async def _person(client: AsyncClient, firstname: str, lastname: str = "Mustermann", **body) -> dict:
    response = await client.post("/persons", json={"firstname": firstname, "lastname": lastname, **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _relate(client: AsyncClient, from_party: dict, type: str, to_party: dict) -> None:
    response = await client.put(f"/parties/{from_party['id']}/relations/{type}/{to_party['id']}")
    assert response.status_code == 200, response.text


def _ids(page: dict) -> set[str]:
    return {item["id"] for item in page["items"]}


@pytest.fixture
async def family(client: AsyncClient) -> dict[str, dict]:
    """A mother with `parent_of` one child and `pays_for` another, a stranger, and a tutor of the first child."""
    mother = await _person(client, "Erika")
    child = await _person(client, "Mia", student={"preferred_meeting_tool": "discord"})
    paid_for = await _person(client, "Paul", "Meier", student={"preferred_meeting_tool": "discord"})
    stranger = await _person(client, "Sven")
    tutor = await _person(client, "Tom", "Lehrer", tutor={})
    await _relate(client, mother, "parent_of", child)
    await _relate(client, mother, "pays_for", paid_for)
    await _relate(client, tutor, "tutor_of", child)
    return {"mother": mother, "child": child, "paid_for": paid_for, "stranger": stranger, "tutor": tutor}


async def test_a_student_reads_their_own_party_and_nothing_else_answers_like_a_missing_one(
    client: AsyncClient, family: dict[str, dict], person_headers
):
    headers = person_headers(uuid.UUID(family["child"]["id"]), Scope.ACCOUNT_SELF, Scope.CRM_READ_OWN)

    own = await client.get(f"/parties/{family['child']['id']}", headers=headers)
    other = await client.get(f"/parties/{family['stranger']['id']}", headers=headers)
    mother = await client.get(f"/parties/{family['mother']['id']}", headers=headers)
    unknown = await client.get(f"/parties/{uuid.uuid4()}", headers=headers)

    assert own.status_code == 200, own.text
    assert own.json()["id"] == family["child"]["id"]
    assert other.status_code == mother.status_code == unknown.status_code == 404
    assert other.json() == mother.json() == unknown.json()
    assert unknown.json()["code"] == "party_not_found"


async def test_a_mother_reads_herself_and_both_children(client: AsyncClient, family: dict[str, dict], person_headers):
    headers = person_headers(uuid.UUID(family["mother"]["id"]), Scope.CRM_READ_OWN)

    for name in ("mother", "child", "paid_for"):
        response = await client.get(f"/parties/{family[name]['id']}", headers=headers)
        assert response.status_code == 200, name
    for name in ("stranger", "tutor"):
        response = await client.get(f"/parties/{family[name]['id']}", headers=headers)
        assert response.status_code == 404, name

    page = (await client.get("/parties", headers=headers)).json()
    assert _ids(page) == {family[name]["id"] for name in ("mother", "child", "paid_for")}
    assert page["total"] == 3


async def test_filters_and_paging_apply_within_the_reach(client: AsyncClient, family: dict[str, dict], person_headers):
    headers = person_headers(uuid.UUID(family["mother"]["id"]), Scope.CRM_READ_OWN)

    students = (await client.get("/parties", params={"role": "student"}, headers=headers)).json()
    searched = (await client.get("/parties", params={"q": "mustermann"}, headers=headers)).json()
    first = (await client.get("/parties", params={"limit": 2}, headers=headers)).json()
    rest = (await client.get("/parties", params={"limit": 2, "offset": 2}, headers=headers)).json()

    assert _ids(students) == {family["child"]["id"], family["paid_for"]["id"]}
    assert students["total"] == 2
    # The stranger is a Mustermann too, but out of reach.
    assert _ids(searched) == {family["mother"]["id"], family["child"]["id"]}
    assert searched["total"] == 2
    assert (len(first["items"]), len(rest["items"])) == (2, 1)
    assert first["total"] == rest["total"] == 3
    assert _ids(first) | _ids(rest) == {family[name]["id"] for name in ("mother", "child", "paid_for")}


async def test_tutor_of_does_not_extend_reach(client: AsyncClient, family: dict[str, dict], person_headers):
    headers = person_headers(uuid.UUID(family["tutor"]["id"]), Scope.CRM_READ_OWN)

    student = await client.get(f"/parties/{family['child']['id']}", headers=headers)
    page = (await client.get("/parties", headers=headers)).json()

    assert student.status_code == 404
    assert _ids(page) == {family["tutor"]["id"]}


async def test_an_application_token_with_crm_read_reads_everything_without_resolving_a_reach(
    client: AsyncClient, family: dict[str, dict], statements: list[str]
):
    statements.clear()

    page = (await client.get("/parties")).json()
    stranger = await client.get(f"/parties/{family['stranger']['id']}")

    assert page["total"] == 5
    assert stranger.status_code == 200
    assert statements
    assert not [statement for statement in statements if "party_relation" in statement]


async def test_a_person_with_crm_read_reads_everything_without_resolving_a_reach(
    client: AsyncClient, family: dict[str, dict], person_headers, statements: list[str]
):
    """An admin holds the unqualified scope: no reach applies to them."""
    headers = person_headers(uuid.UUID(family["mother"]["id"]), Scope.CRM_READ)
    statements.clear()

    page = (await client.get("/parties", headers=headers)).json()
    stranger = await client.get(f"/parties/{family['stranger']['id']}", headers=headers)

    assert page["total"] == 5
    assert stranger.status_code == 200
    assert statements
    assert not [statement for statement in statements if "party_relation" in statement]


async def test_a_restricted_read_resolves_its_reach_with_one_query(
    client: AsyncClient, family: dict[str, dict], person_headers, statements: list[str]
):
    headers = person_headers(uuid.UUID(family["mother"]["id"]), Scope.CRM_READ_OWN)
    statements.clear()

    response = await client.get(f"/parties/{family['child']['id']}", headers=headers)

    assert response.status_code == 200
    assert len([statement for statement in statements if "party_relation" in statement]) == 1


@pytest.mark.parametrize("scope", [Scope.CRM_READ, Scope.CRM_READ_OWN])
async def test_a_reach_aware_request_opens_exactly_one_database_session(
    client: AsyncClient, family: dict[str, dict], person_headers, scope: Scope
):
    """The guard and the endpoint share the request session: `require_access` takes `DBSession`."""
    request_session = app.dependency_overrides[get_db_session]
    opened: list[AsyncSession] = []

    async def counting_session() -> AsyncIterator[AsyncSession]:
        async for session in request_session():
            opened.append(session)
            yield session

    app.dependency_overrides[get_db_session] = counting_session
    headers = person_headers(uuid.UUID(family["mother"]["id"]), scope)

    for path in ("/parties", f"/parties/{family['child']['id']}"):
        opened.clear()
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, path
        assert len(opened) == 1, path
