"""The admin surface of user accounts, run through the real app against the test database."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.audit import AuditEventType, Operator
from app.core.auth.secrets import digest, verify_secret
from app.core.auth.services import users as users_service
from app.core.db.models import (
    AuthAuditLog,
    Party,
    PartyRelation,
    PartyRelationType,
    PreferredMeetingTool,
    Student,
    Tutor,
    UserAccount,
    UserAccountStatus,
    UserSession,
)

pytestmark = pytest.mark.db

INVALID_ACTION_TOKEN = {"detail": "Invalid or expired token", "code": "invalid_action_token"}
USER_ACCOUNT_STATE = "user_account_state"


async def _create(operator: AsyncClient, party: Party, **body: object) -> dict:
    response = await operator.post("/users", json={"party_id": str(party.id), **body})
    assert response.status_code == 201, response.text
    return response.json()


async def _invite(operator: AsyncClient, user_id: str) -> str:
    response = await operator.post(f"/users/{user_id}/invitation")
    assert response.status_code == 201, response.text
    return response.json()["token"]


async def _redeem(operator: AsyncClient, token: str, password: str):
    return await operator.post("/password/redeem", json={"token": token, "new_password": password})


async def _with_password(operator: AsyncClient, party: Party, password: str, email: str = "anna@example.org") -> str:
    """Create an account for ``party`` and set its password; return the account's id."""
    user_id = (await _create(operator, party, email=email))["id"]
    assert (await _redeem(operator, await _invite(operator, user_id), password)).status_code == 204
    return user_id


async def _bystander(operator: AsyncClient, make_person, add_user_session) -> tuple[UUID, UserSession]:
    """Another account, with a live invitation and a live session: what happens to one account leaves it alone."""
    user_id = (await _create(operator, await make_person("Bystander"), email="bystander@example.org"))["id"]
    await _invite(operator, user_id)
    return UUID(user_id), await add_user_session(UUID(user_id))


async def _account(session: AsyncSession, user_id: str) -> UserAccount:
    statement = select(UserAccount).where(UserAccount.id == UUID(user_id)).execution_options(populate_existing=True)
    account = await session.scalar(statement)
    assert account is not None
    return account


async def _revoked(session: AsyncSession, user_session: UserSession) -> str | None:
    await session.refresh(user_session)
    return user_session.revoked_reason


# --- Create --------------------------------------------------------------------------------------


@pytest.mark.parametrize("email", ["anna@example.org", None])
async def test_creating_an_account_answers_201_with_an_active_account_and_no_token(
    operator: AsyncClient, make_person, email: str | None
):
    party = await make_person()

    body = await _create(operator, party, **({"email": email} if email else {}))

    assert body["party_id"] == str(party.id)
    assert body["email"] == email
    assert body["status"] == "active"
    assert body["has_password"] is False
    assert body["roles"] == []
    assert body["last_login_at"] is None
    assert body["locked_until"] is None
    assert "token" not in body
    assert set(body) == {
        "id",
        "party_id",
        "email",
        "status",
        "has_password",
        "roles",
        "last_login_at",
        "created_at",
        "locked_until",
        "updated_at",
    }


async def test_creating_an_account_records_who_created_it(
    operator: AsyncClient, make_person, session: AsyncSession, audit_events
):
    body = await _create(operator, await make_person(), email="anna@example.org", roles=["admin"])

    assert body["roles"] == ["admin"]
    assert await audit_events(UUID(body["id"])) == {AuditEventType.USER_ACCOUNT_CREATED: 1}


async def test_the_same_party_again_is_user_account_already_exists(operator: AsyncClient, make_person):
    party = await make_person()
    await _create(operator, party)

    response = await operator.post("/users", json={"party_id": str(party.id), "email": "other@example.org"})

    assert response.status_code == 409
    assert response.json()["code"] == "user_account_already_exists"


async def test_an_unknown_party_is_422_unknown_account_party(operator: AsyncClient):
    response = await operator.post("/users", json={"party_id": str(uuid4())})

    assert response.status_code == 422
    assert response.json() == {"detail": "Unknown party", "code": "unknown_account_party"}


async def test_a_company_is_account_party_not_a_person(operator: AsyncClient, make_company):
    response = await operator.post("/users", json={"party_id": str((await make_company()).id)})

    assert response.status_code == 422
    assert response.json()["code"] == "account_party_not_a_person"


async def test_another_accounts_email_is_user_email_already_in_use(operator: AsyncClient, make_person):
    await _create(operator, await make_person(), email="anna@example.org")

    response = await operator.post(
        "/users", json={"party_id": str((await make_person("Ben")).id), "email": "ANNA@example.org"}
    )

    assert response.status_code == 409
    assert response.json()["code"] == "user_email_already_in_use"


async def test_the_email_is_stored_lowercased(operator: AsyncClient, make_person, session: AsyncSession):
    body = await _create(operator, await make_person(), email="Anna@Example.org")

    assert body["email"] == "anna@example.org"
    assert (await _account(session, body["id"])).email == "anna@example.org"


@pytest.mark.parametrize("roles", [["tutor"], ["student"], ["guardian"], ["root"]])
async def test_only_stored_roles_can_be_given(operator: AsyncClient, make_person, roles: list[str]):
    response = await operator.post("/users", json={"party_id": str((await make_person()).id), "roles": roles})

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


# --- Read and list -------------------------------------------------------------------------------


async def test_an_unknown_user_is_user_account_not_found(operator: AsyncClient):
    response = await operator.get(f"/users/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["code"] == "user_account_not_found"


async def test_the_list_filters_and_pages_ordered_by_creation_then_id(
    operator: AsyncClient, make_person, session: AsyncSession
):
    anna = await _create(operator, await make_person("Anna"), email="anna@example.org")
    ben = await _create(operator, await make_person("Ben"))
    carla = await _create(operator, await make_person("Carla"), email="carla@example.org")
    # One transaction gives all three the same `created_at`; move Carla's back to see both sort keys.
    (await _account(session, carla["id"])).created_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    await operator.patch(f"/users/{ben['id']}", json={"status": "disabled"})
    anna_then_ben = sorted([anna["id"], ben["id"]], key=UUID)

    everyone = (await operator.get("/users")).json()
    active = (await operator.get("/users", params={"status": "active"})).json()
    by_email = (await operator.get("/users", params={"email": " CARLA@example.org "})).json()
    by_party = (await operator.get("/users", params={"party_id": anna["party_id"]})).json()
    second_page = (await operator.get("/users", params={"limit": 1, "offset": 1})).json()

    assert [item["id"] for item in everyone["items"]] == [carla["id"], *anna_then_ben]
    assert [item["id"] for item in active["items"]] == [carla["id"], anna["id"]]
    assert [item["id"] for item in by_email["items"]] == [carla["id"]]
    assert [item["id"] for item in by_party["items"]] == [anna["id"]]
    assert (second_page["total"], [item["id"] for item in second_page["items"]]) == (3, anna_then_ben[:1])
    assert "locked_until" not in everyone["items"][0]


# --- Derived roles -------------------------------------------------------------------------------


async def test_roles_lists_stored_and_derived_roles(operator: AsyncClient, make_person, session: AsyncSession):
    student = await make_person("Sam")
    guardian = await make_person("Gina")
    tutor = await make_person("Tom")
    session.add_all([
        Student(person_id=student.id, preferred_meeting_tool=PreferredMeetingTool.DISCORD),
        Tutor(person_id=tutor.id),
        PartyRelation(from_party_id=guardian.id, to_party_id=student.id, type=PartyRelationType.PAYS_FOR),
        PartyRelation(from_party_id=tutor.id, to_party_id=student.id, type=PartyRelationType.TUTOR_OF),
    ])
    await session.flush()

    student_account = await _create(operator, student)
    guardian_account = await _create(operator, guardian)
    tutor_account = await _create(operator, tutor, roles=["admin"])
    listed = {item["id"]: item["roles"] for item in (await operator.get("/users")).json()["items"]}

    assert student_account["roles"] == ["student"]
    assert guardian_account["roles"] == ["guardian"]
    # TUTOR_OF does not make a tutor a guardian.
    assert tutor_account["roles"] == ["admin", "tutor"]
    assert listed == {
        student_account["id"]: ["student"],
        guardian_account["id"]: ["guardian"],
        tutor_account["id"]: ["admin", "tutor"],
    }


# --- Stored roles --------------------------------------------------------------------------------


async def test_putting_a_role_is_idempotent_and_removing_it_twice_is_not_found(
    operator: AsyncClient, make_person, audit_events
):
    user_id = (await _create(operator, await make_person()))["id"]

    first = await operator.put(f"/users/{user_id}/roles/admin")
    again = await operator.put(f"/users/{user_id}/roles/admin")
    removed = await operator.delete(f"/users/{user_id}/roles/admin")
    removed_again = await operator.delete(f"/users/{user_id}/roles/admin")

    assert (first.status_code, first.json()["roles"]) == (200, ["admin"])
    assert (again.status_code, again.json()["roles"]) == (200, ["admin"])
    assert removed.status_code == 204
    assert (removed_again.status_code, removed_again.json()["code"]) == (404, "user_role_not_found")
    assert await audit_events(UUID(user_id)) == {
        AuditEventType.USER_ACCOUNT_CREATED: 1,
        AuditEventType.USER_ROLE_ADDED: 1,
        AuditEventType.USER_ROLE_REMOVED: 1,
    }


@pytest.mark.parametrize("role", ["tutor", "root"])
async def test_a_derived_or_unknown_role_is_no_path_value(operator: AsyncClient, make_person, role: str):
    user_id = (await _create(operator, await make_person()))["id"]

    response = await operator.put(f"/users/{user_id}/roles/{role}")

    assert response.status_code == 422


# --- Invitation and reset ------------------------------------------------------------------------


async def test_an_invitation_for_an_account_without_an_email_is_user_account_state(operator: AsyncClient, make_person):
    user_id = (await _create(operator, await make_person()))["id"]

    response = await operator.post(f"/users/{user_id}/invitation")

    assert response.status_code == 409
    assert response.json()["code"] == USER_ACCOUNT_STATE


async def test_an_invitation_for_an_account_with_a_password_is_user_account_state(
    operator: AsyncClient, make_person, password
):
    user_id = await _with_password(operator, await make_person(), password)

    response = await operator.post(f"/users/{user_id}/invitation")

    assert response.status_code == 409
    assert response.json()["code"] == USER_ACCOUNT_STATE


async def test_a_reset_for_an_account_without_a_password_is_user_account_state(operator: AsyncClient, make_person):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]

    response = await operator.post(f"/users/{user_id}/password-reset")

    assert response.status_code == 409
    assert response.json()["code"] == USER_ACCOUNT_STATE


async def test_issuing_invalidates_earlier_unused_tokens_of_the_same_purpose(
    operator: AsyncClient, make_person, add_user_session, audit_events, password, live_tokens
):
    bystander, _ = await _bystander(operator, make_person, add_user_session)
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    first = await _invite(operator, user_id)
    second = await _invite(operator, user_id)

    [live] = await live_tokens(UUID(user_id))
    replaced = await _redeem(operator, first, password)

    assert live.token_hash == digest(second)
    assert first.startswith("sf_ua_")
    assert (replaced.status_code, replaced.json()) == (422, INVALID_ACTION_TOKEN)
    assert (await _redeem(operator, second, password)).status_code == 204
    assert (await audit_events(UUID(user_id)))[AuditEventType.INVITATION_ISSUED] == 2
    assert len(await live_tokens(bystander)) == 1


async def test_a_reset_token_expires_after_the_configured_hours(
    operator: AsyncClient, make_person, auth_settings, password
):
    user_id = await _with_password(operator, await make_person(), password)
    before = datetime.now(UTC)

    response = await operator.post(f"/users/{user_id}/password-reset")

    expires_at = datetime.fromisoformat(response.json()["expires_at"])
    assert response.status_code == 201
    assert set(response.json()) == {"token", "expires_at"}
    assert (
        timedelta(0)
        <= expires_at - before - timedelta(hours=auth_settings.password_reset_expire_hours)
        < timedelta(minutes=1)
    )


# --- Redeem --------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [UserAccountStatus.ACTIVE, UserAccountStatus.DISABLED])
async def test_redeeming_an_invitation_sets_the_password_and_leaves_the_status(
    operator: AsyncClient, make_person, session: AsyncSession, audit_events, status: UserAccountStatus, password
):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    await operator.patch(f"/users/{user_id}", json={"status": status})
    token = await _invite(operator, user_id)

    response = await _redeem(operator, token, password)

    account = await _account(session, user_id)
    assert response.status_code == 204
    assert account.status is status
    assert account.password_hash is not None
    assert verify_secret(password, account.password_hash)
    assert (await operator.get(f"/users/{user_id}")).json()["has_password"] is True
    assert (await audit_events(UUID(user_id)))[AuditEventType.PASSWORD_SET] == 1


async def test_redeeming_clears_the_failed_login_counter_and_the_lock(
    operator: AsyncClient, make_person, session: AsyncSession, password
):
    user_id = await _with_password(operator, await make_person(), password)
    account = await _account(session, user_id)
    account.failed_login_count = 7
    account.locked_until = datetime.now(UTC) + timedelta(minutes=5)
    await session.flush()
    token = (await operator.post(f"/users/{user_id}/password-reset")).json()["token"]

    assert (await _redeem(operator, token, "a new password")).status_code == 204

    account = await _account(session, user_id)
    assert (account.failed_login_count, account.locked_until) == (0, None)


async def test_a_used_an_expired_an_unknown_and_a_replaced_token_answer_the_same_body(
    operator: AsyncClient, make_person, session: AsyncSession, password, live_tokens
):
    used_user = (await _create(operator, await make_person("Anna"), email="anna@example.org"))["id"]
    used = await _invite(operator, used_user)
    await _redeem(operator, used, password)
    expired_user = (await _create(operator, await make_person("Ben"), email="ben@example.org"))["id"]
    expired = await _invite(operator, expired_user)
    [expiring] = await live_tokens(UUID(expired_user))
    expiring.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    replaced_user = (await _create(operator, await make_person("Carla"), email="carla@example.org"))["id"]
    replaced = await _invite(operator, replaced_user)
    await _invite(operator, replaced_user)

    answers = [await _redeem(operator, token, password) for token in (used, expired, "sf_ua_unknown", replaced)]

    assert [(answer.status_code, answer.json()) for answer in answers] == [(422, INVALID_ACTION_TOKEN)] * 4


async def test_redeeming_a_reset_revokes_every_session_and_an_invitation_none(
    operator: AsyncClient, make_person, session: AsyncSession, add_user_session, audit_events, password, live_tokens
):
    bystander, bystanders_session = await _bystander(operator, make_person, add_user_session)
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    before_invitation = await add_user_session(UUID(user_id))
    assert (await _redeem(operator, await _invite(operator, user_id), password)).status_code == 204
    assert await _revoked(session, before_invitation) is None

    other = await add_user_session(UUID(user_id))
    reset = (await operator.post(f"/users/{user_id}/password-reset")).json()["token"]
    assert (await _redeem(operator, reset, "a new password")).status_code == 204

    assert await _revoked(session, before_invitation) == "password_reset"
    assert await _revoked(session, other) == "password_reset"
    assert (await audit_events(UUID(user_id)))[AuditEventType.SESSION_REVOKED] == 1
    assert await _revoked(session, bystanders_session) is None
    assert len(await live_tokens(bystander)) == 1


@pytest.mark.parametrize("length", [7, 129])
async def test_a_password_of_7_or_129_characters_is_weak_password(
    operator: AsyncClient, make_person, session: AsyncSession, length: int, live_tokens
):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    token = await _invite(operator, user_id)

    response = await _redeem(operator, token, "x" * length)

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Password must be between 8 and 128 characters long",
        "code": "weak_password",
    }
    assert (await _account(session, user_id)).password_hash is None
    assert len(await live_tokens(UUID(user_id))) == 1


@pytest.mark.parametrize("length", [8, 128])
async def test_a_password_of_8_or_128_characters_is_accepted(
    operator: AsyncClient, make_person, session: AsyncSession, length: int
):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]

    response = await _redeem(operator, await _invite(operator, user_id), "x" * length)

    password_hash = (await _account(session, user_id)).password_hash
    assert response.status_code == 204
    assert password_hash is not None
    assert verify_secret("x" * length, password_hash)


# --- Update --------------------------------------------------------------------------------------


async def test_disabling_revokes_the_sessions(
    operator: AsyncClient, make_person, session: AsyncSession, add_user_session, audit_events, live_tokens
):
    bystander, bystanders_session = await _bystander(operator, make_person, add_user_session)
    user_id = (await _create(operator, await make_person()))["id"]
    live = await add_user_session(UUID(user_id))

    response = await operator.patch(f"/users/{user_id}", json={"status": "disabled"})

    assert (response.status_code, response.json()["status"]) == (200, "disabled")
    assert await _revoked(session, live) == "account_disabled"
    assert await _revoked(session, bystanders_session) is None
    assert len(await live_tokens(bystander)) == 1
    assert await audit_events(UUID(user_id)) == {
        AuditEventType.USER_ACCOUNT_CREATED: 1,
        AuditEventType.USER_ACCOUNT_DISABLED: 1,
        AuditEventType.SESSION_REVOKED: 1,
    }


async def test_enabling_an_account_without_a_password_succeeds(operator: AsyncClient, make_person, audit_events):
    user_id = (await _create(operator, await make_person()))["id"]
    await operator.patch(f"/users/{user_id}", json={"status": "disabled"})

    response = await operator.patch(f"/users/{user_id}", json={"status": "active"})

    assert (response.status_code, response.json()["status"], response.json()["has_password"]) == (200, "active", False)
    assert (await audit_events(UUID(user_id)))[AuditEventType.USER_ACCOUNT_ENABLED] == 1


@pytest.mark.parametrize("new_email", ["new@example.org", None])
async def test_changing_or_removing_the_email_invalidates_unused_action_tokens(
    operator: AsyncClient,
    make_person,
    add_user_session,
    audit_events,
    new_email: str | None,
    password,
    live_tokens,
):
    bystander, _ = await _bystander(operator, make_person, add_user_session)
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    token = await _invite(operator, user_id)

    response = await operator.patch(f"/users/{user_id}", json={"email": new_email})

    assert (response.status_code, response.json()["email"]) == (200, new_email)
    assert await live_tokens(UUID(user_id)) == []
    assert (await _redeem(operator, token, password)).json() == INVALID_ACTION_TOKEN
    assert (await audit_events(UUID(user_id)))[AuditEventType.USER_ACCOUNT_UPDATED] == 1
    assert len(await live_tokens(bystander)) == 1


async def test_changing_the_email_invalidates_an_outstanding_password_reset(
    operator: AsyncClient, make_person, password, live_tokens
):
    user_id = await _with_password(operator, await make_person(), password)
    reset = (await operator.post(f"/users/{user_id}/password-reset")).json()["token"]

    response = await operator.patch(f"/users/{user_id}", json={"email": "new@example.org"})

    assert response.status_code == 200
    assert await live_tokens(UUID(user_id)) == []
    assert (await _redeem(operator, reset, "a new password")).json() == INVALID_ACTION_TOKEN


async def test_removing_the_email_of_an_account_with_a_password_is_user_account_state(
    operator: AsyncClient, make_person, session: AsyncSession, password
):
    user_id = await _with_password(operator, await make_person(), password)

    response = await operator.patch(f"/users/{user_id}", json={"email": None})

    assert (response.status_code, response.json()["code"]) == (409, USER_ACCOUNT_STATE)
    assert (await _account(session, user_id)).email == "anna@example.org"


async def test_changing_the_email_to_another_accounts_is_user_email_already_in_use(operator: AsyncClient, make_person):
    await _create(operator, await make_person("Anna"), email="anna@example.org")
    user_id = (await _create(operator, await make_person("Ben"), email="ben@example.org"))["id"]

    response = await operator.patch(f"/users/{user_id}", json={"email": "Anna@Example.org"})

    assert (response.status_code, response.json()["code"]) == (409, "user_email_already_in_use")


@pytest.mark.parametrize(
    "body", [{}, {"email": "anna@example.org"}, {"email": " Anna@Example.org"}, {"status": "active"}]
)
async def test_a_patch_that_changes_nothing_records_nothing(
    operator: AsyncClient, make_person, session: AsyncSession, audit_events, body: dict, live_tokens
):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    await _invite(operator, user_id)
    before = await audit_events(UUID(user_id))

    response = await operator.patch(f"/users/{user_id}", json=body)

    assert response.status_code == 200
    assert await audit_events(UUID(user_id)) == before
    assert len(await live_tokens(UUID(user_id))) == 1


# --- Sessions ------------------------------------------------------------------------------------


async def test_revoking_the_sessions_revokes_every_live_one_and_leaves_the_rest(
    operator: AsyncClient, make_person, session: AsyncSession, add_user_session, audit_events
):
    _, bystanders_session = await _bystander(operator, make_person, add_user_session)
    user_id = (await _create(operator, await make_person()))["id"]
    first, second, earlier, expired = [await add_user_session(UUID(user_id)) for _ in range(4)]
    earlier.revoked_at, earlier.revoked_reason = datetime.now(UTC) - timedelta(hours=1), "logout"
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    earlier_revoked_at = earlier.revoked_at

    response = await operator.delete(f"/users/{user_id}/sessions")

    assert response.status_code == 204
    reasons = [await _revoked(session, s) for s in (first, second, earlier, expired, bystanders_session)]
    assert reasons == ["admin", "admin", "logout", None, None]
    assert earlier.revoked_at == earlier_revoked_at
    assert (await audit_events(UUID(user_id)))[AuditEventType.SESSION_REVOKED] == 1


async def test_revoking_the_sessions_of_an_unknown_user_is_user_account_not_found(operator: AsyncClient):
    response = await operator.delete(f"/users/{uuid4()}/sessions")

    assert (response.status_code, response.json()["code"]) == (404, "user_account_not_found")


async def test_the_audit_details_name_the_operator_and_never_the_email(
    operator: AsyncClient, make_person, session: AsyncSession
):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    await operator.patch(f"/users/{user_id}", json={"email": "new@example.org"})
    await operator.put(f"/users/{user_id}/roles/admin")

    details = list(await session.scalars(select(AuthAuditLog.detail).where(AuthAuditLog.principal_id == user_id)))

    assert details
    assert all(re.fullmatch(r".+ by application:[0-9a-f-]{36}\.", detail or "") for detail in details)
    assert not any("example.org" in (detail or "") for detail in details)


# --- Text that is no text ------------------------------------------------------------------------


async def _redeem_raw(operator: AsyncClient, token: str, password: str):
    """Redeem with a body written by hand: a lone surrogate is valid JSON only as an escape."""
    body = f'{{"token": "{token}", "new_password": "{password}"}}'
    return await operator.post("/password/redeem", content=body, headers={"Content-Type": "application/json"})


async def test_a_token_with_a_lone_surrogate_is_invalid_action_token(operator: AsyncClient, password):
    response = await _redeem_raw(operator, "sf_ua_\\ud800", password)

    assert (response.status_code, response.json()) == (422, INVALID_ACTION_TOKEN)


async def test_a_password_with_a_lone_surrogate_is_weak_password(
    operator: AsyncClient, make_person, session: AsyncSession, live_tokens
):
    user_id = (await _create(operator, await make_person(), email="anna@example.org"))["id"]
    token = await _invite(operator, user_id)

    response = await _redeem_raw(operator, token, "a long password \\ud800")

    assert (response.status_code, response.json()["code"]) == (422, "weak_password")
    assert (await _account(session, user_id)).password_hash is None
    assert len(await live_tokens(UUID(user_id))) == 1


# --- The canonical e-mail address ----------------------------------------------------------------


@pytest.mark.parametrize(
    "spelling",
    [
        "anna@xn--bcher-kva.de",
        "Anna@Bücher.DE",
        "Anna Schmidt <anna@bücher.de>",
        " anna@bücher.de ",
    ],
)
async def test_every_spelling_of_an_address_is_one_canonical_address(
    operator: AsyncClient, make_person, session: AsyncSession, spelling: str
):
    """Punycode, case, a display name and NFD all land on the stored form - for create, filter and conflict."""
    created = await _create(operator, await make_person(), email=spelling)

    listed = (await operator.get("/users", params={"email": spelling})).json()
    conflict = await operator.post("/users", json={"party_id": str((await make_person("Ben")).id), "email": spelling})

    assert created["email"] == "anna@bücher.de"
    assert (await _account(session, created["id"])).email == "anna@bücher.de"
    assert [item["id"] for item in listed["items"]] == [created["id"]]
    assert (conflict.status_code, conflict.json()["code"]) == (409, "user_email_already_in_use")


async def test_the_list_filter_refuses_what_is_no_address(operator: AsyncClient):
    response = await operator.get("/users", params={"email": "not an address"})

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")


async def test_a_violation_of_another_constraint_is_not_an_email_conflict(
    operator: AsyncClient, make_person, session: AsyncSession, monkeypatch
):
    """Only the e-mail's unique constraint is `user_email_already_in_use`; anything else surfaces as it is."""
    party = await make_person()
    await _create(operator, party, email="anna@example.org")

    async def no_account(*_args: object) -> None:
        return None

    # Past the party check, a second account for the party violates `user_account_party_id_key`.
    monkeypatch.setattr(users_service, "find_user_account_by_party", no_account)
    with pytest.raises(IntegrityError):
        await users_service.create_user_account(
            session, party_id=party.id, email="other@example.org", actor=Operator.CLI
        )
