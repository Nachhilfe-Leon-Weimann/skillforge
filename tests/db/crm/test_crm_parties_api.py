"""`/persons`, `/companies` and `GET /parties/{party_id}` against the real database."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ContactInfo, Party

pytestmark = pytest.mark.db

EMAIL = {"type": "email", "value": "max.mustermann@example.com", "label": "private"}
PHONE = {"type": "phone", "value": "0151234567"}


async def _create_person(client: AsyncClient, **body) -> dict:
    response = await client.post("/persons", json={"firstname": "Max", "lastname": "Mustermann", **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _create_company(client: AsyncClient, **body) -> dict:
    response = await client.post("/companies", json={"name": "Musterfirma GmbH", **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _party_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Party)) or 0


def _validation_locs(response) -> list[list]:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "validation_error"
    return [error["loc"] for error in body["errors"]]


# --- create and read ---


async def test_create_person_answers_201_with_the_detail_and_a_location(client: AsyncClient):
    response = await client.post(
        "/persons", json={"firstname": "Max", "lastname": "Mustermann", "contact_infos": [PHONE, EMAIL]}
    )

    assert response.status_code == 201
    detail = response.json()
    assert response.headers["location"] == f"/api/v1/crm/parties/{detail['id']}"
    assert detail == {
        "type": "person",
        "id": detail["id"],
        "display_name": "Max Mustermann",
        "firstname": "Max",
        "lastname": "Mustermann",
        "student": None,
        "tutor": None,
        "contact_infos": [
            {"id": detail["contact_infos"][0]["id"], **EMAIL},
            {"id": detail["contact_infos"][1]["id"], **PHONE, "label": None},
        ],
        "created_at": detail["created_at"],
        "updated_at": detail["updated_at"],
    }
    assert detail["created_at"] and detail["updated_at"]


async def test_create_company_answers_201_with_the_detail_and_a_location(client: AsyncClient):
    response = await client.post("/companies", json={"name": "Musterfirma GmbH", "contact_infos": [EMAIL]})

    assert response.status_code == 201
    detail = response.json()
    assert response.headers["location"] == f"/api/v1/crm/parties/{detail['id']}"
    assert detail == {
        "type": "company",
        "id": detail["id"],
        "display_name": "Musterfirma GmbH",
        "name": "Musterfirma GmbH",
        "contact_infos": [{"id": detail["contact_infos"][0]["id"], **EMAIL}],
        "created_at": detail["created_at"],
        "updated_at": detail["updated_at"],
    }


@pytest.mark.parametrize("create", [_create_person, _create_company], ids=["person", "company"])
async def test_the_location_of_a_created_party_reads_the_same_detail(client: AsyncClient, create):
    created = await create(client, contact_infos=[EMAIL, PHONE])
    location = f"/api/v1/crm/parties/{created['id']}"

    response = await client.get(f"http://testserver{location}")

    assert response.status_code == 200
    assert response.json() == created


async def test_get_party_of_an_unknown_id_is_404(client: AsyncClient):
    response = await client.get(f"/parties/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Party not found", "code": "party_not_found"}


# --- update ---


async def test_update_person_changes_only_the_fields_that_are_sent(client: AsyncClient):
    person = await _create_person(client, contact_infos=[EMAIL])

    response = await client.patch(f"/persons/{person['id']}", json={"lastname": "  Musterfrau "})

    assert response.status_code == 200
    assert response.json() == {
        **person,
        "lastname": "Musterfrau",
        "display_name": "Max Musterfrau",
        "updated_at": response.json()["updated_at"],
    }


async def test_update_company_changes_the_name(client: AsyncClient):
    company = await _create_company(client)

    response = await client.patch(f"/companies/{company['id']}", json={"name": " Musterfirma AG "})

    assert response.status_code == 200
    assert response.json() == {
        **company,
        "name": "Musterfirma AG",
        "display_name": "Musterfirma AG",
        "updated_at": response.json()["updated_at"],
    }


@pytest.mark.parametrize(
    ("create", "collection"), [(_create_person, "persons"), (_create_company, "companies")], ids=["person", "company"]
)
async def test_update_with_an_empty_body_changes_nothing(client: AsyncClient, backdate, create, collection: str):
    created = await create(client)
    await backdate(uuid.UUID(created["id"]))
    before = (await client.get(f"/parties/{created['id']}")).json()

    response = await client.patch(f"/{collection}/{created['id']}", json={})

    assert response.status_code == 200
    assert response.json() == before


async def test_the_detail_shows_the_party_timestamp_a_write_has_moved(client: AsyncClient, backdate, updated_at):
    person = await _create_person(client)
    await backdate(uuid.UUID(person["id"]))
    before = await updated_at(uuid.UUID(person["id"]))

    response = await client.patch(f"/persons/{person['id']}", json={"firstname": "Maximilian"})

    after = await updated_at(uuid.UUID(person["id"]))
    assert after > before
    assert response.json()["updated_at"] == after.isoformat().replace("+00:00", "Z")
    assert response.json()["created_at"] == person["created_at"]


async def test_update_person_with_a_company_id_is_404_person_not_found(client: AsyncClient):
    company = await _create_company(client)

    response = await client.patch(f"/persons/{company['id']}", json={"firstname": "Max"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Person not found", "code": "person_not_found"}


async def test_update_company_with_a_person_id_is_404_company_not_found(client: AsyncClient):
    person = await _create_person(client)

    response = await client.patch(f"/companies/{person['id']}", json={"name": "Musterfirma"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Company not found", "code": "company_not_found"}


@pytest.mark.parametrize("collection", ["persons", "companies"])
async def test_update_of_an_unknown_id_is_404(client: AsyncClient, collection: str):
    response = await client.patch(f"/{collection}/{uuid.uuid4()}", json={})

    assert response.status_code == 404
    assert response.json()["code"] == {"persons": "person_not_found", "companies": "company_not_found"}[collection]


# --- names ---


async def test_names_are_stripped_on_create(client: AsyncClient):
    person = await _create_person(client, firstname="  Max ", lastname="\tMustermann\n")
    company = await _create_company(client, name="  Musterfirma GmbH  ")

    assert (person["firstname"], person["lastname"], person["display_name"]) == ("Max", "Mustermann", "Max Mustermann")
    assert company["name"] == "Musterfirma GmbH"


@pytest.mark.parametrize("name", ["", "   ", None, "x" * 201])
async def test_create_rejects_a_blank_missing_or_oversized_name(client: AsyncClient, session: AsyncSession, name):
    person = await client.post("/persons", json={"firstname": name, "lastname": "Mustermann"})
    company = await client.post("/companies", json={"name": name})

    assert _validation_locs(person) == [["body", "firstname"]]
    assert _validation_locs(company) == [["body", "name"]]
    assert await _party_count(session) == 0


@pytest.mark.parametrize("name", ["", "   ", None])
async def test_update_rejects_a_blank_name_and_an_explicit_null(client: AsyncClient, name):
    person = await _create_person(client)
    company = await _create_company(client)

    patched_person = await client.patch(f"/persons/{person['id']}", json={"firstname": name})
    patched_company = await client.patch(f"/companies/{company['id']}", json={"name": name})

    # The `Name | MISSING` union reports one error per member, all under the same field.
    assert {tuple(loc[:2]) for loc in _validation_locs(patched_person)} == {("body", "firstname")}
    assert {tuple(loc[:2]) for loc in _validation_locs(patched_company)} == {("body", "name")}
    assert (await client.get(f"/parties/{person['id']}")).json() == person
    assert (await client.get(f"/parties/{company['id']}")).json() == company


# --- nested contact infos ---


async def test_an_email_is_stored_lowercased_and_a_phone_without_whitespace(client: AsyncClient, session: AsyncSession):
    person = await _create_person(
        client,
        contact_infos=[
            {"type": "email", "value": "  Max.Mustermann@Example.COM "},
            {"type": "phone", "value": " +49 151 234 567\t"},
        ],
    )

    stored = (await session.execute(select(ContactInfo.type, ContactInfo.value))).all()
    assert sorted((type.value, value) for type, value in stored) == [
        ("email", "max.mustermann@example.com"),
        ("phone", "+49151234567"),
    ]
    assert [info["value"] for info in person["contact_infos"]] == ["max.mustermann@example.com", "+49151234567"]


@pytest.mark.parametrize(
    "contact_info",
    [
        {"type": "email", "value": "not-an-email"},
        {"type": "email", "value": ""},
        {"type": "phone", "value": " \t "},
    ],
    ids=["invalid e-mail", "empty e-mail", "blank phone"],
)
@pytest.mark.parametrize("collection", ["persons", "companies"])
async def test_create_rejects_an_invalid_contact_value_with_its_field_path(
    client: AsyncClient, session: AsyncSession, collection: str, contact_info: dict
):
    names = {"firstname": "Max", "lastname": "Mustermann"} if collection == "persons" else {"name": "Musterfirma"}

    response = await client.post(f"/{collection}", json={**names, "contact_infos": [EMAIL, contact_info]})

    assert _validation_locs(response) == [["body", "contact_infos", 1, "value"]]
    assert await _party_count(session) == 0


@pytest.mark.parametrize(
    "duplicate",
    [
        [EMAIL, EMAIL],
        [EMAIL, {"type": "email", "value": " MAX.Mustermann@example.com", "label": "work"}],
        [PHONE, {"type": "phone", "value": "0151 234 567"}],
    ],
    ids=["identical", "e-mail differing in case and label", "phone differing in whitespace"],
)
@pytest.mark.parametrize("collection", ["persons", "companies"])
async def test_create_rejects_the_same_type_and_value_twice_with_a_field_path(
    client: AsyncClient, session: AsyncSession, collection: str, duplicate: list[dict]
):
    names = {"firstname": "Max", "lastname": "Mustermann"} if collection == "persons" else {"name": "Musterfirma"}

    response = await client.post(f"/{collection}", json={**names, "contact_infos": duplicate})

    assert _validation_locs(response) == [["body", "contact_infos"]]
    assert await _party_count(session) == 0


async def test_the_same_value_may_appear_with_another_type_and_on_another_party(client: AsyncClient):
    """No global duplicate prevention: a parent's e-mail may legitimately appear on the child too."""
    parent = await _create_person(client, contact_infos=[EMAIL])
    child = await _create_person(client, firstname="Mia", contact_infos=[EMAIL])

    assert parent["contact_infos"][0]["value"] == child["contact_infos"][0]["value"]
    assert parent["contact_infos"][0]["id"] != child["contact_infos"][0]["id"]


async def test_a_validation_error_never_echoes_the_rejected_contact_value(client: AsyncClient):
    response = await client.post(
        "/persons",
        json={"firstname": "Max", "lastname": "Mustermann", "contact_infos": [{"type": "email", "value": "secret-at"}]},
    )

    assert response.status_code == 422
    assert "secret-at" not in response.text
