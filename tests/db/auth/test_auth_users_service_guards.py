"""What holds when two requests interleave inside one account's service calls.

These reach past the API on purpose: the friendly pre-checks are check-then-insert, so the tests
take them out of the way (or write behind the service's back) to reach the guard underneath.
"""

import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from app.core.auth import AuthSettings
from app.core.auth.services import users
from app.core.auth.services.action_tokens import issue_action_token
from app.core.auth.services.errors import (
    UserAccountAlreadyExistsError,
    UserAccountStateError,
    UserEmailAlreadyInUseError,
)
from app.core.auth.services.users import invite_user_account, update_user_account
from app.core.db.models import UserAccount, UserActionTokenPurpose

pytestmark = pytest.mark.db

SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))


async def _invite(session, party, email: str = "anna@example.org"):
    return await invite_user_account(session, SETTINGS, party_id=party.id, email=email, actor="test")


async def _set_password_behind_the_session(session, user_id: uuid.UUID) -> None:
    """Write the column the way another transaction would: the loaded object keeps its old value."""
    await session.execute(
        update(UserAccount)
        .where(UserAccount.id == user_id)
        .values(password_hash="$argon2id$written-by-somebody-else")
        .execution_options(synchronize_session=False)
    )


# --- The row lock re-reads the row (finding 2) ---


async def test_issuing_reads_the_account_under_the_lock_not_what_the_session_held(session, make_person):
    """`bootstrap_admin_account` finds the account unlocked, so the state check after the lock
    must not run on the attributes that look-up loaded."""
    party = await make_person()
    created = await _invite(session, party)
    account = await users.find_user_account_by_party(session, party.id)
    assert account is not None and account.password_hash is None, "the session holds an account without a password"
    await _set_password_behind_the_session(session, created.view.account.id)

    with pytest.raises(UserAccountStateError):
        await issue_action_token(
            session,
            SETTINGS,
            user_id=created.view.account.id,
            purpose=UserActionTokenPurpose.INVITATION,
            actor="test",
        )


# --- The constraint has the last word (finding 3) ---


async def test_a_party_that_got_an_account_between_check_and_insert_is_a_conflict(session, make_person, monkeypatch):
    party = await make_person()
    await _invite(session, party)

    async def blind(_session, _party_id):
        return None

    monkeypatch.setattr(users, "find_user_account_by_party", blind)

    with pytest.raises(UserAccountAlreadyExistsError):
        await _invite(session, party, email="second@example.org")


async def test_an_email_taken_between_check_and_insert_is_a_conflict(session, make_person, monkeypatch):
    first, second = await make_person(), await make_person("Bea")
    await _invite(session, first)

    monkeypatch.setattr(users, "_require_email_unused", _blind_email_check)

    with pytest.raises(UserEmailAlreadyInUseError):
        await _invite(session, second, email="anna@example.org")


async def test_an_email_taken_between_check_and_flush_of_a_patch_is_a_conflict(session, make_person, monkeypatch):
    first, second = await make_person(), await make_person("Bea")
    await _invite(session, first)
    created = await _invite(session, second, email="bea@example.org")

    monkeypatch.setattr(users, "_require_email_unused", _blind_email_check)

    with pytest.raises(UserEmailAlreadyInUseError):
        await update_user_account(session, created.view.account.id, email="anna@example.org", actor="test")


async def test_the_transaction_survives_a_translated_conflict(session, make_person, monkeypatch):
    """The violation happens in a SAVEPOINT, so the caller can go on working."""
    first, second = await make_person(), await make_person("Bea")
    await _invite(session, first)
    monkeypatch.setattr(users, "_require_email_unused", _blind_email_check)
    with pytest.raises(UserEmailAlreadyInUseError):
        await _invite(session, second, email="anna@example.org")

    monkeypatch.undo()
    created = await _invite(session, second, email="bea@example.org")

    assert (
        await session.scalar(select(UserAccount.email).where(UserAccount.id == created.view.account.id))
        == "bea@example.org"
    )


async def _blind_email_check(_session, _email) -> None:
    return None
