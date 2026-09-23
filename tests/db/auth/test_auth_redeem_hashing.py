"""Argon2 is slow on purpose, so it must not run while the account's row is locked."""

import pytest
from pydantic import SecretStr

from app.core.auth import AuthSettings
from app.core.auth.services import action_tokens
from app.core.auth.services.action_tokens import redeem_action_token
from app.core.auth.services.errors import InvalidActionTokenError, WeakPasswordError
from app.core.auth.services.users import invite_user_account

pytestmark = pytest.mark.db

SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
PASSWORD = "correct horse battery staple"


@pytest.fixture
def steps(monkeypatch) -> list[str]:
    """Record the order of the two slow steps of a redeem."""
    recorded: list[str] = []
    hash_secret, lock = action_tokens.hash_secret, action_tokens.lock_user_account

    def record_hash(password: str) -> str:
        recorded.append("hash")
        return hash_secret(password)

    async def record_lock(session, user_id):
        recorded.append("lock")
        return await lock(session, user_id)

    monkeypatch.setattr(action_tokens, "hash_secret", record_hash)
    monkeypatch.setattr(action_tokens, "lock_user_account", record_lock)
    return recorded


async def _invite(session, party, email: str = "anna@example.org") -> str:
    created = await invite_user_account(session, SETTINGS, party_id=party.id, email=email, actor="test")
    return created.invitation.plaintext


async def test_the_password_is_hashed_before_the_account_row_is_locked(session, make_person, steps):
    token = await _invite(session, await make_person())
    steps.clear()

    await redeem_action_token(session, plaintext=token, new_password=PASSWORD, actor="test")

    assert steps == ["hash", "lock"], "every other request on the account waits out the hash"


async def test_an_invalid_token_is_refused_without_hashing_anything(session, make_person, steps):
    await _invite(session, await make_person())
    steps.clear()

    with pytest.raises(InvalidActionTokenError):
        await redeem_action_token(session, plaintext="sf_ua_unknown", new_password=PASSWORD, actor="test")

    assert steps == []


async def test_a_password_outside_the_policy_is_refused_without_hashing_it(session, make_person, steps):
    token = await _invite(session, await make_person())
    steps.clear()

    with pytest.raises(WeakPasswordError):
        await redeem_action_token(session, plaintext=token, new_password="short", actor="test")

    assert steps == []
