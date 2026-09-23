"""The admin surface of `/api/v1/auth/users` against the database."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.auth.services.action_tokens import ACTION_TOKEN_PREFIX
from app.core.db.models import (
    AuthAuditLog,
    UserAccount,
    UserAccountRole,
    UserAccountRoleName,
    UserActionToken,
    UserSession,
)

pytestmark = pytest.mark.db


async def test_inviting_a_person_party_answers_201_with_an_invited_account_and_a_token(client, make_person):
    party = await make_person()

    response = await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})

    assert response.status_code == 201
    body = response.json()
    assert (body["party_id"], body["email"], body["status"], body["roles"]) == (
        str(party.id),
        "anna@example.org",
        "invited",
        [],
    )
    assert body["invitation"]["token"].startswith(ACTION_TOKEN_PREFIX)
    assert body["invitation"]["expires_at"]


async def test_the_invitation_token_is_stored_as_a_hash_only(client, make_person, session):
    party = await make_person()

    response = await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})

    plaintext = response.json()["invitation"]["token"]
    hashes = list(await session.scalars(select(UserActionToken.token_hash)))
    assert hashes
    assert plaintext not in hashes


async def test_inviting_stores_the_email_lowercased(client, make_person, session):
    party = await make_person()

    response = await client.post("/users", json={"party_id": str(party.id), "email": "Anna@Example.org"})

    assert response.json()["email"] == "anna@example.org"
    assert await session.scalar(select(UserAccount.email).where(UserAccount.party_id == party.id)) == "anna@example.org"


async def test_inviting_the_same_party_again_is_a_conflict(client, make_person):
    party = await make_person()
    await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})

    response = await client.post("/users", json={"party_id": str(party.id), "email": "other@example.org"})

    assert response.status_code == 409
    assert response.json()["code"] == "user_account_already_exists"


async def test_inviting_a_company_party_is_rejected(client, make_company):
    party = await make_company()

    response = await client.post("/users", json={"party_id": str(party.id), "email": "office@example.org"})

    assert response.status_code == 422
    assert response.json()["code"] == "account_party_not_a_person"


async def test_inviting_an_unknown_party_is_not_found(client):
    response = await client.post("/users", json={"party_id": str(uuid4()), "email": "anna@example.org"})

    assert response.status_code == 404
    assert response.json()["code"] == "account_party_not_found"


async def test_inviting_with_an_email_another_account_uses_is_a_conflict(client, make_person):
    first, second = await make_person(), await make_person("Bea")
    await client.post("/users", json={"party_id": str(first.id), "email": "anna@example.org"})

    response = await client.post("/users", json={"party_id": str(second.id), "email": "ANNA@example.org"})

    assert response.status_code == 409
    assert response.json()["code"] == "user_email_already_in_use"


async def test_inviting_with_a_stored_role_gives_the_account_that_role(client, make_person, session):
    party = await make_person()

    response = await client.post(
        "/users", json={"party_id": str(party.id), "email": "anna@example.org", "roles": ["admin"]}
    )

    assert response.json()["roles"] == ["admin"]
    stored = list(await session.scalars(select(UserAccountRole.role)))
    assert stored == [UserAccountRoleName.ADMIN]


async def test_inviting_with_a_derived_role_is_a_request_validation_error(client, make_person):
    party = await make_person()

    response = await client.post(
        "/users", json={"party_id": str(party.id), "email": "anna@example.org", "roles": ["tutor"]}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- Reading ---


async def test_the_list_is_ordered_by_email_and_filters(client, make_person):
    for index, email in enumerate(["carla@example.org", "anna@example.org", "bea@example.org"]):
        party = await make_person(f"Person{index}")
        await client.post("/users", json={"party_id": str(party.id), "email": email})

    response = await client.get("/users")

    body = response.json()
    assert body["total"] == 3
    assert [item["email"] for item in body["items"]] == [
        "anna@example.org",
        "bea@example.org",
        "carla@example.org",
    ]


async def test_the_list_filters_by_status_party_and_email(client, make_person):
    first, second = await make_person(), await make_person("Bea")
    await client.post("/users", json={"party_id": str(first.id), "email": "anna@example.org"})
    await client.post("/users", json={"party_id": str(second.id), "email": "bea@example.org"})

    by_party = await client.get("/users", params={"party_id": str(second.id)})
    by_email = await client.get("/users", params={"email": "ANNA@Example.org"})
    by_status = await client.get("/users", params={"status": "active"})

    assert [item["email"] for item in by_party.json()["items"]] == ["bea@example.org"]
    assert [item["email"] for item in by_email.json()["items"]] == ["anna@example.org"]
    assert by_status.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


async def test_reading_an_unknown_account_is_not_found(client):
    response = await client.get(f"/users/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "user_account_not_found"


# --- Updating ---


async def test_patching_the_email_stores_it_lowercased(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    response = await client.patch(f"/users/{user_id}", json={"email": "Anna.Neu@Example.org"})

    assert response.status_code == 200
    assert response.json()["email"] == "anna.neu@example.org"


async def test_patching_to_an_email_another_account_uses_is_a_conflict(client, make_person):
    first, second = await make_person(), await make_person("Bea")
    await client.post("/users", json={"party_id": str(first.id), "email": "anna@example.org"})
    user_id = (await client.post("/users", json={"party_id": str(second.id), "email": "bea@example.org"})).json()["id"]

    response = await client.patch(f"/users/{user_id}", json={"email": "anna@example.org"})

    assert response.status_code == 409
    assert response.json()["code"] == "user_email_already_in_use"


async def test_an_empty_patch_changes_nothing(client, make_person):
    party = await make_person()
    created = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()

    response = await client.patch(f"/users/{created['id']}", json={})

    assert response.status_code == 200
    assert response.json()["email"] == created["email"]
    assert response.json()["status"] == "invited"


async def test_enabling_an_account_without_a_password_is_rejected(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    response = await client.patch(f"/users/{user_id}", json={"status": "active"})

    assert response.status_code == 409
    assert response.json()["code"] == "user_account_state"


async def test_patching_the_status_to_invited_is_a_request_validation_error(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    response = await client.patch(f"/users/{user_id}", json={"status": "invited"})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_disabling_an_account_revokes_its_sessions(client, make_person, add_user_session, session):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]
    user_session = await add_user_session(user_id)

    response = await client.patch(f"/users/{user_id}", json={"status": "disabled"})

    assert response.json()["status"] == "disabled"
    await session.refresh(user_session)
    assert user_session.revoked_at is not None
    assert user_session.revoked_reason == "account_disabled"


async def test_enabling_a_disabled_account_with_a_password_brings_it_back_without_its_sessions(
    client, make_person, add_user_session, session
):
    party = await make_person()
    created = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()
    user_id, token = created["id"], created["invitation"]["token"]
    await client.post("/password/redeem", json={"token": token, "new_password": "correct horse battery staple"})
    user_session = await add_user_session(user_id)
    await client.patch(f"/users/{user_id}", json={"status": "disabled"})

    response = await client.patch(f"/users/{user_id}", json={"status": "active"})

    assert response.json()["status"] == "active"
    await session.refresh(user_session)
    assert user_session.revoked_reason == "account_disabled", "a revoked session stays revoked"
    assert [log.event_type for log in await session.scalars(select(AuthAuditLog).order_by(AuthAuditLog.created_at))][
        -1
    ] == "user_account.enabled"


# --- Stored roles ---


async def test_putting_a_stored_role_is_idempotent(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    first = await client.put(f"/users/{user_id}/roles/admin")
    second = await client.put(f"/users/{user_id}/roles/admin")

    assert first.status_code == second.status_code == 200
    assert first.json()["roles"] == second.json()["roles"] == ["admin"]


async def test_removing_a_stored_role_answers_204(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]
    await client.put(f"/users/{user_id}/roles/admin")

    response = await client.delete(f"/users/{user_id}/roles/admin")

    assert response.status_code == 204
    assert (await client.get(f"/users/{user_id}")).json()["roles"] == []


async def test_removing_a_role_the_account_does_not_hold_is_not_found(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    response = await client.delete(f"/users/{user_id}/roles/admin")

    assert response.status_code == 404
    assert response.json()["code"] == "user_role_not_found"


async def test_an_unknown_role_is_a_request_validation_error(client, make_person):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]

    response = await client.put(f"/users/{user_id}/roles/wizard")

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- Sessions ---


async def test_revoking_the_sessions_of_an_account_answers_204(client, make_person, add_user_session, session):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]
    user_session = await add_user_session(user_id)

    response = await client.delete(f"/users/{user_id}/sessions")

    assert response.status_code == 204
    await session.refresh(user_session)
    assert user_session.revoked_reason == "admin"


async def test_revoking_the_sessions_of_an_unknown_account_is_not_found(client):
    response = await client.delete(f"/users/{uuid4()}/sessions")

    assert response.status_code == 404
    assert response.json()["code"] == "user_account_not_found"


async def test_revoking_leaves_an_already_revoked_session_alone(client, make_person, add_user_session, session):
    party = await make_person()
    user_id = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()["id"]
    user_session = await add_user_session(user_id)
    await client.delete(f"/users/{user_id}/sessions")
    await session.refresh(user_session)
    revoked_at = user_session.revoked_at

    await client.delete(f"/users/{user_id}/sessions")

    await session.refresh(user_session)
    assert user_session.revoked_at == revoked_at
    assert list(await session.scalars(select(UserSession.revoked_reason))) == ["admin"]
