"""Issuing and redeeming the one-time tokens of `/api/v1/auth`."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.auth.secrets import digest
from app.core.auth.services.action_tokens import ACTION_TOKEN_PREFIX
from app.core.db.models import UserAccount, UserActionToken

pytestmark = pytest.mark.db

PASSWORD = "correct horse battery staple"
INVALID_TOKEN_BODY = {"detail": "Invalid or expired token", "code": "invalid_action_token"}


async def _invite(client, make_person, email: str = "anna@example.org") -> tuple[str, str]:
    """Invite a fresh person and return ``(user_id, invitation token)``."""
    party = await make_person()
    body = (await client.post("/users", json={"party_id": str(party.id), "email": email})).json()
    return body["id"], body["invitation"]["token"]


async def _redeem(client, token: str, password: str = PASSWORD):
    return await client.post("/password/redeem", json={"token": token, "new_password": password})


async def _expire(session, token: str) -> None:
    await session.execute(
        update(UserActionToken)
        .where(UserActionToken.token_hash == digest(token))
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )


async def test_redeeming_an_invitation_sets_the_password_and_activates_the_account(client, make_person, session):
    user_id, token = await _invite(client, make_person)

    response = await _redeem(client, token)

    assert response.status_code == 204
    account = await session.get(UserAccount, user_id)
    await session.refresh(account)
    assert account.status.value == "active"
    assert account.password_hash is not None
    assert PASSWORD not in account.password_hash


async def test_the_redeemed_token_is_marked_used(client, make_person, session):
    _, token = await _invite(client, make_person)

    await _redeem(client, token)

    used_at = await session.scalar(select(UserActionToken.used_at).where(UserActionToken.token_hash == digest(token)))
    assert used_at is not None


async def test_redeeming_the_same_token_twice_is_rejected(client, make_person):
    _, token = await _invite(client, make_person)
    await _redeem(client, token)

    response = await _redeem(client, token, "another password entirely")

    assert response.status_code == 422
    assert response.json() == INVALID_TOKEN_BODY


async def test_every_rejected_token_answers_the_same_body(client, make_person, session):
    """Unknown, used, expired and replaced must not be told apart (spec: no enumeration)."""
    _, used = await _invite(client, make_person, "used@example.org")
    await _redeem(client, used)
    _, expired = await _invite(client, make_person, "expired@example.org")
    await _expire(session, expired)
    replaced_user, replaced = await _invite(client, make_person, "replaced@example.org")
    await client.post(f"/users/{replaced_user}/invitation")

    answers = [
        (await _redeem(client, candidate)).json()
        for candidate in [f"{ACTION_TOKEN_PREFIX}unknown-token", used, expired, replaced]
    ]

    assert answers == [INVALID_TOKEN_BODY] * 4


async def test_a_rejected_token_changes_nothing(client, make_person, session):
    user_id, token = await _invite(client, make_person)
    await _expire(session, token)

    await _redeem(client, token)

    account = await session.get(UserAccount, user_id)
    await session.refresh(account)
    assert account.password_hash is None
    assert account.status.value == "invited"


@pytest.mark.parametrize("password", ["short", "x" * 129])
async def test_a_password_outside_the_policy_is_rejected(client, make_person, password: str):
    _, token = await _invite(client, make_person)

    response = await _redeem(client, token, password)

    assert response.status_code == 422
    assert response.json()["code"] == "weak_password"


async def test_a_rejected_password_leaves_the_token_usable(client, make_person):
    _, token = await _invite(client, make_person)
    await _redeem(client, token, "short")

    assert (await _redeem(client, token)).status_code == 204


# --- Sessions ---


async def test_redeeming_an_invitation_revokes_no_session(client, make_person, add_user_session, session):
    user_id, token = await _invite(client, make_person)
    user_session = await add_user_session(user_id)

    await _redeem(client, token)

    await session.refresh(user_session)
    assert user_session.revoked_at is None


async def test_redeeming_a_password_reset_revokes_every_session_of_the_account(
    client, make_person, add_user_session, session
):
    user_id, invitation = await _invite(client, make_person)
    await _redeem(client, invitation)
    first, second = await add_user_session(user_id), await add_user_session(user_id)
    reset = (await client.post(f"/users/{user_id}/password-reset")).json()["token"]

    await _redeem(client, reset, "an entirely different password")

    for user_session in (first, second):
        await session.refresh(user_session)
        assert user_session.revoked_reason == "password_reset"


async def test_redeeming_keeps_a_disabled_account_disabled(client, make_person, session):
    user_id, invitation = await _invite(client, make_person)
    await _redeem(client, invitation)
    await client.patch(f"/users/{user_id}", json={"status": "disabled"})
    reset = (await client.post(f"/users/{user_id}/password-reset")).json()["token"]

    await _redeem(client, reset, "an entirely different password")

    account = await session.get(UserAccount, user_id)
    await session.refresh(account)
    assert account.status.value == "disabled"


# --- Issuing ---


async def test_a_fresh_invitation_invalidates_the_previous_one(client, make_person, session):
    user_id, first = await _invite(client, make_person)

    second = (await client.post(f"/users/{user_id}/invitation")).json()["token"]

    assert (await _redeem(client, first)).status_code == 422
    assert (await _redeem(client, second)).status_code == 204
    live = list(await session.scalars(select(UserActionToken.invalidated_at)))
    assert sum(1 for invalidated_at in live if invalidated_at is None) == 1


async def test_an_invitation_for_an_account_with_a_password_is_refused(client, make_person):
    user_id, invitation = await _invite(client, make_person)
    await _redeem(client, invitation)

    response = await client.post(f"/users/{user_id}/invitation")

    assert response.status_code == 409
    assert response.json()["code"] == "user_account_state"


async def test_a_password_reset_for_an_account_without_a_password_is_refused(client, make_person):
    user_id, _ = await _invite(client, make_person)

    response = await client.post(f"/users/{user_id}/password-reset")

    assert response.status_code == 409
    assert response.json()["code"] == "user_account_state"


async def test_a_password_reset_revokes_nothing_before_it_is_redeemed(client, make_person, add_user_session, session):
    user_id, invitation = await _invite(client, make_person)
    await _redeem(client, invitation)
    user_session = await add_user_session(user_id)

    await client.post(f"/users/{user_id}/password-reset")

    await session.refresh(user_session)
    assert user_session.revoked_at is None


async def test_issuing_for_an_unknown_account_is_not_found(client):
    response = await client.post("/users/00000000-0000-0000-0000-0000000000dd/invitation")

    assert response.status_code == 404
    assert response.json()["code"] == "user_account_not_found"
