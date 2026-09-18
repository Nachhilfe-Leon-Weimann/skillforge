"""Text Postgres cannot store is a validation 422, never a driver error behind a 500.

Postgres rejects U+0000 in ``text``, and asyncpg cannot encode a lone UTF-16 surrogate - its error
message would repeat the value, which for a contact info is personal data. Both are checks that
need no database, so they belong to the request models (error rule I-2 of the CRM API spec).
"""

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import Party, Subject

pytestmark = pytest.mark.db

NUL = "\x00"
LONE_SURROGATE = "\ud83d"


async def _post(client: AsyncClient, path: str, body: dict):
    # httpx would refuse to encode a lone surrogate; ``json.dumps`` escapes it, as a real client can.
    return await client.post(path, content=json.dumps(body), headers={"Content-Type": "application/json"})


async def _count(session: AsyncSession, model) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.parametrize("bad", [NUL, LONE_SURROGATE], ids=["nul", "lone surrogate"])
@pytest.mark.parametrize(
    ("path", "body", "loc"),
    [
        ("/subjects", {"title": "Ma{bad}ths"}, ["body", "title"]),
        ("/persons", {"firstname": "Ma{bad}x", "lastname": "Mustermann"}, ["body", "firstname"]),
        ("/persons", {"firstname": "Max", "lastname": "Muster{bad}mann"}, ["body", "lastname"]),
        ("/companies", {"name": "Muster{bad}firma"}, ["body", "name"]),
    ],
    ids=["subject title", "firstname", "lastname", "company name"],
)
async def test_create_rejects_unstorable_text_in_a_name(
    client: AsyncClient, session: AsyncSession, path: str, body: dict, loc: list, bad: str
):
    response = await _post(client, path, {key: value.format(bad=bad) for key, value in body.items()})

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
    assert [error["loc"] for error in response.json()["errors"]] == [loc]
    assert await _count(session, Party) == 0
    assert await _count(session, Subject) == 0


@pytest.mark.parametrize("bad", [NUL, LONE_SURROGATE], ids=["nul", "lone surrogate"])
@pytest.mark.parametrize(
    ("contact_info", "field"),
    [
        ({"type": "phone", "value": "+49 170 1234567{bad}"}, "value"),
        ({"type": "email", "value": "max{bad}@example.com"}, "value"),
        ({"type": "phone", "value": "+49 170 1234567", "label": "pri{bad}vate"}, "label"),
    ],
    ids=["phone value", "email value", "label"],
)
async def test_create_rejects_unstorable_text_in_a_contact_info(
    client: AsyncClient, session: AsyncSession, contact_info: dict, field: str, bad: str
):
    contact_info = {key: value.format(bad=bad) for key, value in contact_info.items()}

    response = await _post(
        client, "/persons", {"firstname": "Max", "lastname": "Mustermann", "contact_infos": [contact_info]}
    )

    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["errors"]] == [["body", "contact_infos", 0, field]]
    assert "1234567" not in response.text
    assert await _count(session, Party) == 0


@pytest.mark.parametrize("bad", [NUL, LONE_SURROGATE], ids=["nul", "lone surrogate"])
async def test_update_rejects_unstorable_text_in_a_name(client: AsyncClient, bad: str):
    subject = (await client.post("/subjects", json={"title": "Maths"})).json()
    person = (await client.post("/persons", json={"firstname": "Max", "lastname": "Mustermann"})).json()
    company = (await client.post("/companies", json={"name": "Musterfirma"})).json()

    responses = [
        await _patch(client, f"/subjects/{subject['id']}", {"title": f"Ma{bad}ths"}),
        await _patch(client, f"/persons/{person['id']}", {"lastname": f"Muster{bad}mann"}),
        await _patch(client, f"/companies/{company['id']}", {"name": f"Muster{bad}firma"}),
    ]

    assert [response.status_code for response in responses] == [422, 422, 422]
    assert (await client.get(f"/parties/{person['id']}")).json() == person
    assert (await client.get(f"/parties/{company['id']}")).json() == company


async def _patch(client: AsyncClient, path: str, body: dict):
    return await client.patch(path, content=json.dumps(body), headers={"Content-Type": "application/json"})
