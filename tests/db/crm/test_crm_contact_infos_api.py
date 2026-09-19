"""`/parties/{party_id}/contact-infos` against the real database."""

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ContactInfo, ContactInfoType
from app.services.crm import contact_infos, persons
from app.services.crm.errors import ContactInfoAlreadyExistsError, InvalidContactValueError
from app.services.crm.inputs import NewContactInfo

pytestmark = pytest.mark.db

EMAIL = {"type": "email", "value": "max.mustermann@example.com", "label": "private"}
ALREADY_EXISTS = {"detail": "Contact info already exists for this party", "code": "contact_info_already_exists"}
NOT_FOUND = {"detail": "Contact info not found", "code": "contact_info_not_found"}
INVALID_VALUE = {"detail": "Value is not valid for this contact info type", "code": "invalid_contact_value"}


async def _person(client: AsyncClient, *contact_infos: dict, firstname: str = "Max") -> dict:
    body = {"firstname": firstname, "lastname": "Mustermann", "contact_infos": list(contact_infos)}
    response = await client.post("/persons", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# As long as an address may be: 64 characters before the "@", labels of at most 63 behind it.
LONGEST_EMAIL = f"{'a' * 64}@{'b' * 63}.{'c' * 63}.{'d' * 57}.com"


async def _stored(session: AsyncSession, party_id: str) -> list[tuple[str, str, str | None]]:
    rows = await session.execute(
        select(ContactInfo.type, ContactInfo.value, ContactInfo.label).where(
            ContactInfo.party_id == uuid.UUID(party_id)
        )
    )
    return sorted((type.value, value, label) for type, value, label in rows)


# --- POST ---


async def test_post_answers_201_with_the_normalized_contact_info(client: AsyncClient, session: AsyncSession):
    person = await _person(client)

    response = await client.post(
        f"/parties/{person['id']}/contact-infos",
        json={"type": "phone", "value": " +49 151 234 567 ", "label": " mobile "},
    )

    assert response.status_code == 201
    created = response.json()
    assert created == {"id": created["id"], "type": "phone", "value": "+49151234567", "label": "mobile"}
    assert (await client.get(f"/parties/{person['id']}")).json()["contact_infos"] == [created]
    assert await _stored(session, person["id"]) == [("phone", "+49151234567", "mobile")]


async def test_post_works_for_a_company_as_well(client: AsyncClient):
    company = (await client.post("/companies", json={"name": "Musterfirma GmbH"})).json()

    response = await client.post(f"/parties/{company['id']}/contact-infos", json=EMAIL)

    assert response.status_code == 201
    assert (await client.get(f"/parties/{company['id']}")).json()["contact_infos"] == [response.json()]


@pytest.mark.parametrize(
    "duplicate",
    [EMAIL, {"type": "email", "value": " MAX.Mustermann@Example.COM ", "label": "work"}],
    ids=["identical", "differing in case, whitespace and label"],
)
async def test_post_of_a_duplicate_type_and_value_is_409_and_writes_nothing(
    client: AsyncClient, session: AsyncSession, duplicate: dict
):
    person = await _person(client, EMAIL)

    response = await client.post(f"/parties/{person['id']}/contact-infos", json=duplicate)

    assert response.status_code == 409
    assert response.json() == ALREADY_EXISTS
    assert "mustermann" not in response.text.lower()
    assert await _stored(session, person["id"]) == [("email", "max.mustermann@example.com", "private")]


async def test_the_same_value_is_fine_on_another_party(client: AsyncClient):
    await _person(client, EMAIL)
    other = await _person(client, firstname="Mia")

    same_on_other_party = await client.post(f"/parties/{other['id']}/contact-infos", json=EMAIL)

    assert same_on_other_party.status_code == 201


async def test_a_value_is_judged_by_its_type(client: AsyncClient):
    """No string is both: an e-mail address is no phone number, and a phone number is no e-mail address."""
    person = await _person(client)
    url = f"/parties/{person['id']}/contact-infos"

    address_as_phone = await client.post(url, json={"type": "phone", "value": EMAIL["value"]})
    number_as_email = await client.post(url, json={"type": "email", "value": "0171 1234567"})

    assert (address_as_phone.status_code, number_as_email.status_code) == (422, 422)
    assert [error["loc"] for error in address_as_phone.json()["errors"]] == [["body", "value"]]


async def test_post_to_an_unknown_party_is_404_party_not_found(client: AsyncClient):
    response = await client.post(f"/parties/{uuid.uuid4()}/contact-infos", json=EMAIL)

    assert response.status_code == 404
    assert response.json() == {"detail": "Party not found", "code": "party_not_found"}


@pytest.mark.parametrize(
    "body",
    [{"type": "email", "value": "not-an-email"}, {"type": "phone", "value": "  "}, {"type": "email", "value": ""}],
    ids=["invalid e-mail", "blank phone", "empty e-mail"],
)
async def test_post_of_an_invalid_value_is_the_validation_422_with_a_field_path(
    client: AsyncClient, session: AsyncSession, body: dict
):
    person = await _person(client)

    response = await client.post(f"/parties/{person['id']}/contact-infos", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert [error["loc"] for error in response.json()["errors"]] == [["body", "value"]]
    assert await _stored(session, person["id"]) == []


# --- PATCH ---


async def test_patch_changes_the_value_and_normalizes_it_against_the_stored_type(
    client: AsyncClient, session: AsyncSession
):
    person = await _person(client, EMAIL, {"type": "phone", "value": "0151234567"})
    email, phone = person["contact_infos"]

    patched_email = await client.patch(
        f"/parties/{person['id']}/contact-infos/{email['id']}", json={"value": "  Max@NEW.Example.com "}
    )
    patched_phone = await client.patch(
        f"/parties/{person['id']}/contact-infos/{phone['id']}", json={"value": "030 12 34 56"}
    )

    assert (patched_email.status_code, patched_phone.status_code) == (200, 200)
    assert patched_email.json() == {**email, "value": "max@new.example.com"}
    assert patched_phone.json() == {**phone, "value": "+4930123456"}
    assert await _stored(session, person["id"]) == [
        ("email", "max@new.example.com", "private"),
        ("phone", "+4930123456", None),
    ]


@pytest.mark.parametrize(
    "value", ["not-an-email", "0151 234 567", "max@", "  "], ids=["text", "phone", "cut off", "blank"]
)
async def test_patch_of_an_email_with_a_non_email_value_is_422_invalid_contact_value(
    client: AsyncClient, session: AsyncSession, value: str
):
    person = await _person(client, EMAIL)
    email = person["contact_infos"][0]

    response = await client.patch(f"/parties/{person['id']}/contact-infos/{email['id']}", json={"value": value})

    assert response.status_code == 422
    assert response.json() == INVALID_VALUE
    assert await _stored(session, person["id"]) == [("email", "max.mustermann@example.com", "private")]


async def test_patch_of_a_phone_with_only_whitespace_is_422_invalid_contact_value(client: AsyncClient):
    person = await _person(client, {"type": "phone", "value": "0151234567"})
    phone = person["contact_infos"][0]

    response = await client.patch(f"/parties/{person['id']}/contact-infos/{phone['id']}", json={"value": " \t "})

    assert (response.status_code, response.json()) == (422, INVALID_VALUE)


@pytest.mark.parametrize(
    "value",
    ["max@example.com", "0171 1234567 (Mama)", "112", "0171 1234567 ext. 12"],
    ids=["e-mail", "a note", "local only", "extension"],
)
async def test_patch_of_a_phone_with_what_has_no_e164_form_is_422_invalid_contact_value(
    client: AsyncClient, session: AsyncSession, value: str
):
    person = await _person(client, {"type": "phone", "value": "0151234567"})
    phone = person["contact_infos"][0]

    response = await client.patch(f"/parties/{person['id']}/contact-infos/{phone['id']}", json={"value": value})

    assert (response.status_code, response.json()) == (422, INVALID_VALUE)
    assert await _stored(session, person["id"]) == [("phone", "+49151234567", None)]


async def test_post_and_patch_store_a_phone_number_in_the_same_form(client: AsyncClient, session: AsyncSession):
    person = await _person(client, {"type": "phone", "value": "0171 1234567"})
    posted = person["contact_infos"][0]
    url = f"/parties/{person['id']}/contact-infos"
    other = (await client.post(url, json={"type": "phone", "value": "030 1234567"})).json()

    patched = await client.patch(f"{url}/{other['id']}", json={"value": "0049 (0)172 7654321"})

    assert posted["value"] == "+491711234567"
    assert patched.json()["value"] == "+491727654321"
    assert [value for _, value, _ in await _stored(session, person["id"])] == ["+491711234567", "+491727654321"]


async def test_another_spelling_of_a_phone_number_the_party_has_is_409(client: AsyncClient, session: AsyncSession):
    """``0171 ...`` and ``+49 171 ...`` are one number, so the unique key of the party sees one value."""
    person = await _person(client, {"type": "phone", "value": "0171 1234567"})
    url = f"/parties/{person['id']}/contact-infos"
    other = (await client.post(url, json={"type": "phone", "value": "030 1234567"})).json()

    posted = await client.post(url, json={"type": "phone", "value": "+49 171 1234567"})
    patched = await client.patch(f"{url}/{other['id']}", json={"value": "0049 171 123 45 67"})

    assert [(r.status_code, r.json()["code"]) for r in (posted, patched)] == [
        (409, "contact_info_already_exists"),
        (409, "contact_info_already_exists"),
    ]
    assert [value for _, value, _ in await _stored(session, person["id"])] == ["+491711234567", "+49301234567"]


async def test_patch_cannot_change_the_type(client: AsyncClient, session: AsyncSession):
    person = await _person(client, EMAIL)
    email = person["contact_infos"][0]

    response = await client.patch(
        f"/parties/{person['id']}/contact-infos/{email['id']}", json={"type": "phone", "label": "work"}
    )

    assert response.status_code == 200
    assert response.json() == {**email, "label": "work"}
    assert await _stored(session, person["id"]) == [("email", "max.mustermann@example.com", "work")]


async def test_patch_with_a_null_label_clears_it_and_a_label_is_stripped(client: AsyncClient):
    person = await _person(client, EMAIL)
    url = f"/parties/{person['id']}/contact-infos/{person['contact_infos'][0]['id']}"

    cleared = await client.patch(url, json={"label": None})
    relabelled = await client.patch(url, json={"label": "  work "})

    assert (cleared.status_code, cleared.json()["label"], cleared.json()["value"]) == (200, None, EMAIL["value"])
    assert relabelled.json()["label"] == "work"


async def test_patch_with_an_empty_body_changes_nothing(client: AsyncClient, backdate, updated_at):
    person = await _person(client, EMAIL)
    await backdate(uuid.UUID(person["id"]))
    before = await updated_at(uuid.UUID(person["id"]))

    response = await client.patch(f"/parties/{person['id']}/contact-infos/{person['contact_infos'][0]['id']}", json={})

    assert response.status_code == 200
    assert response.json() == person["contact_infos"][0]
    assert await updated_at(uuid.UUID(person["id"])) == before


@pytest.mark.parametrize(
    ("body", "field"),
    [({"value": None}, "value"), ({"value": ""}, "value"), ({"label": ""}, "label"), ({"label": "   "}, "label")],
    ids=["null value", "empty value", "empty label", "blank label"],
)
async def test_patch_rejects_a_null_value_and_blank_fields(client: AsyncClient, body: dict, field: str):
    person = await _person(client, EMAIL)

    response = await client.patch(
        f"/parties/{person['id']}/contact-infos/{person['contact_infos'][0]['id']}", json=body
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert {tuple(error["loc"][:2]) for error in response.json()["errors"]} == {("body", field)}


@pytest.mark.parametrize("bad", ["\x00", "\ud83d"], ids=["nul", "lone surrogate"])
@pytest.mark.parametrize("field", ["value", "label"])
async def test_patch_rejects_unstorable_text_as_a_validation_error(client: AsyncClient, field: str, bad: str):
    person = await _person(client, {"type": "phone", "value": "0151234567"})
    url = f"/parties/{person['id']}/contact-infos/{person['contact_infos'][0]['id']}"

    response = await client.patch(
        url, content=json.dumps({field: f"0151{bad}99"}), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "0151" not in response.text


async def test_patch_into_an_existing_type_and_value_is_409_and_keeping_its_own_value_is_not(
    client: AsyncClient, session: AsyncSession
):
    person = await _person(client, EMAIL, {"type": "email", "value": "second@example.com"})
    first, second = person["contact_infos"]
    assert (first["value"], second["value"]) == (EMAIL["value"], "second@example.com")

    conflict = await client.patch(
        f"/parties/{person['id']}/contact-infos/{second['id']}", json={"value": "MAX.MUSTERMANN@example.com"}
    )
    own_value = await client.patch(
        f"/parties/{person['id']}/contact-infos/{second['id']}", json={"value": "Second@Example.com", "label": "work"}
    )

    assert (conflict.status_code, conflict.json()) == (409, ALREADY_EXISTS)
    assert own_value.status_code == 200
    assert await _stored(session, person["id"]) == [
        ("email", "max.mustermann@example.com", "private"),
        ("email", "second@example.com", "work"),
    ]


# --- bounds ---


async def test_an_oversized_contact_value_is_the_validation_422_on_every_write(
    client: AsyncClient, session: AsyncSession
):
    """The value sits in the index of uq_contact_info: unbounded, it fails there as a 500."""
    person = await _person(client, {"type": "phone", "value": "0151234567"})
    url = f"/parties/{person['id']}/contact-infos"
    oversized = "1" * 255
    huge = "1" * 5000

    nested = await client.post(
        "/persons",
        json={"firstname": "Mia", "lastname": "M", "contact_infos": [{"type": "phone", "value": huge}]},
    )
    posted = await client.post(url, json={"type": "phone", "value": oversized})
    patched = await client.patch(f"{url}/{person['contact_infos'][0]['id']}", json={"value": huge})
    fits = await client.post(url, json={"type": "email", "value": LONGEST_EMAIL})

    assert [error["loc"] for error in nested.json()["errors"]] == [["body", "contact_infos", 0, "value"]]
    assert [error["loc"] for error in posted.json()["errors"]] == [["body", "value"]]
    assert {tuple(error["loc"][:2]) for error in patched.json()["errors"]} == {("body", "value")}
    assert [r.status_code for r in (nested, posted, patched, fits)] == [422, 422, 422, 201]
    assert "11111" not in nested.text + posted.text + patched.text
    assert len(LONGEST_EMAIL) == 254
    assert [value for _, value, _ in await _stored(session, person["id"])] == [LONGEST_EMAIL, "+49151234567"]


async def test_two_spellings_of_one_address_are_a_duplicate_on_every_route(client: AsyncClient, session: AsyncSession):
    """``J`` + caron and U+01F0 normalize to the same address - in the request model, not only in the service."""
    decomposed, composed = "J\u030cx@example.com", "\u01f0x@example.com"

    nested = await client.post(
        "/persons",
        json={
            "firstname": "Mia",
            "lastname": "M",
            "contact_infos": [{"type": "email", "value": decomposed}, {"type": "email", "value": composed}],
        },
    )
    person = await _person(client, {"type": "email", "value": decomposed})
    posted = await client.post(f"/parties/{person['id']}/contact-infos", json={"type": "email", "value": composed})

    assert nested.status_code == 422
    assert [error["loc"] for error in nested.json()["errors"]] == [["body", "contact_infos"]]
    assert (posted.status_code, posted.json()) == (409, ALREADY_EXISTS)
    assert await _stored(session, person["id"]) == [("email", composed, None)]


async def test_post_and_patch_store_the_same_form_of_an_address(client: AsyncClient):
    person = await _person(
        client, {"type": "email", "value": "J\u030cx@example.com"}, {"type": "email", "value": "b@x.de"}
    )
    second = person["contact_infos"][0 if person["contact_infos"][0]["value"] == "b@x.de" else 1]

    response = await client.patch(
        f"/parties/{person['id']}/contact-infos/{second['id']}", json={"value": "J\u030cx@Example.com"}
    )

    assert (response.status_code, response.json()) == (409, ALREADY_EXISTS)


# --- another party's contact info ---


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("owner", ["another party", "nobody", "unknown party"])
async def test_a_contact_info_id_that_is_not_the_partys_is_404(
    client: AsyncClient, session: AsyncSession, method: str, owner: str
):
    person = await _person(client, EMAIL)
    other = await _person(client, {"type": "email", "value": "mia@example.com"}, firstname="Mia")
    party_id, contact_info_id = {
        "another party": (person["id"], other["contact_infos"][0]["id"]),
        "nobody": (person["id"], str(uuid.uuid4())),
        "unknown party": (str(uuid.uuid4()), person["contact_infos"][0]["id"]),
    }[owner]

    response = await client.request(
        method, f"/parties/{party_id}/contact-infos/{contact_info_id}", json={"value": "hijacked@example.com"}
    )

    assert (response.status_code, response.json()) == (404, NOT_FOUND)
    assert await _stored(session, person["id"]) == [("email", "max.mustermann@example.com", "private")]
    assert await _stored(session, other["id"]) == [("email", "mia@example.com", None)]


# --- DELETE ---


async def test_delete_answers_204_and_removes_only_that_contact_info(client: AsyncClient, session: AsyncSession):
    person = await _person(client, EMAIL, {"type": "phone", "value": "0151234567"})
    email, phone = person["contact_infos"]

    response = await client.delete(f"/parties/{person['id']}/contact-infos/{email['id']}")
    again = await client.delete(f"/parties/{person['id']}/contact-infos/{email['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert (again.status_code, again.json()) == (404, NOT_FOUND)
    assert (await client.get(f"/parties/{person['id']}")).json()["contact_infos"] == [phone]


# --- the aggregate root ---


@pytest.mark.parametrize("write", ["post", "patch value", "patch label", "clear label", "delete"])
async def test_every_contact_info_write_moves_the_partys_updated_at(
    client: AsyncClient, backdate, updated_at, write: str
):
    person = await _person(client, EMAIL)
    other = await _person(client, firstname="Mia")
    url = f"/parties/{person['id']}/contact-infos"
    own = f"{url}/{person['contact_infos'][0]['id']}"
    await backdate(uuid.UUID(person["id"]), uuid.UUID(other["id"]))
    before = await updated_at(uuid.UUID(person["id"]))
    untouched = await updated_at(uuid.UUID(other["id"]))

    response = await {
        "post": lambda: client.post(url, json={"type": "phone", "value": "0151234567"}),
        "patch value": lambda: client.patch(own, json={"value": "new@example.com"}),
        "patch label": lambda: client.patch(own, json={"label": "work"}),
        "clear label": lambda: client.patch(own, json={"label": None}),
        "delete": lambda: client.delete(own),
    }[write]()

    assert response.status_code in (200, 201, 204)
    after = await updated_at(uuid.UUID(person["id"]))
    assert after > before
    assert await updated_at(uuid.UUID(other["id"])) == untouched
    detail = (await client.get(f"/parties/{person['id']}")).json()
    assert detail["updated_at"] == after.isoformat().replace("+00:00", "Z")


@pytest.mark.parametrize("write", ["post duplicate", "patch into duplicate", "patch invalid"])
async def test_a_refused_contact_info_write_leaves_updated_at_alone(
    client: AsyncClient, backdate, updated_at, write: str
):
    person = await _person(client, EMAIL, {"type": "email", "value": "second@example.com"})
    url = f"/parties/{person['id']}/contact-infos"
    second = f"{url}/{person['contact_infos'][1]['id']}"
    await backdate(uuid.UUID(person["id"]))
    before = await updated_at(uuid.UUID(person["id"]))

    response = await {
        "post duplicate": lambda: client.post(url, json=EMAIL),
        "patch into duplicate": lambda: client.patch(second, json={"value": EMAIL["value"]}),
        "patch invalid": lambda: client.patch(second, json={"value": "nope"}),
    }[write]()

    assert response.status_code in (409, 422)
    assert await updated_at(uuid.UUID(person["id"])) == before


# --- the services, for in-process callers ---


async def test_a_conflict_leaves_the_surrounding_transaction_usable(session: AsyncSession):
    """The violation happens inside a SAVEPOINT - not in a flush that precedes it."""
    party = await persons.create_person(session, firstname="Max", lastname="Mustermann")
    first = await contact_infos.add_contact_info(session, party.id, type=ContactInfoType.EMAIL, value="a@example.com")
    second = await contact_infos.add_contact_info(session, party.id, type=ContactInfoType.EMAIL, value="b@example.com")
    first_id, second_id = first.id, second.id

    with pytest.raises(ContactInfoAlreadyExistsError):
        await contact_infos.add_contact_info(session, party.id, type=ContactInfoType.EMAIL, value="A@Example.com")
    with pytest.raises(ContactInfoAlreadyExistsError):
        await contact_infos.update_contact_info(session, party.id, second_id, value="a@example.com")

    # Without a real SAVEPOINT the session would now answer PendingRollbackError.
    third = await contact_infos.add_contact_info(session, party.id, type=ContactInfoType.PHONE, value="0151 234 567")
    assert third.value == "+49151234567"
    assert await _stored(session, str(party.id)) == [
        ("email", "a@example.com", None),
        ("email", "b@example.com", None),
        ("phone", "+49151234567", None),
    ]
    assert first_id != second_id


async def test_the_services_refuse_what_the_request_models_would_have_caught(session: AsyncSession):
    party = await persons.create_person(session, firstname="Max", lastname="Mustermann")

    with pytest.raises(InvalidContactValueError) as invalid:
        await contact_infos.add_contact_info(session, party.id, type=ContactInfoType.EMAIL, value="secret-not-an-email")
    with pytest.raises(InvalidContactValueError):
        await persons.create_person(
            session, firstname="Mia", lastname="M", contact_infos=[NewContactInfo(ContactInfoType.PHONE, "  ")]
        )
    with pytest.raises(ContactInfoAlreadyExistsError) as duplicate:
        await persons.create_person(
            session,
            firstname="Mia",
            lastname="M",
            contact_infos=[
                NewContactInfo(ContactInfoType.EMAIL, "Mia@Example.com"),
                NewContactInfo(ContactInfoType.EMAIL, " mia@example.com"),
            ],
        )

    assert "secret" not in str(invalid.value) and invalid.value.__cause__ is None
    assert "mia@" not in str(duplicate.value).lower()
