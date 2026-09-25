"""The person grants at the service level, where the clock can be set: the lockout's growth and the reuse grace."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings, IssuedUserToken, TokenDenial, issue_user_token, refresh_user_token
from app.core.auth.audit import AuditEventType
from app.core.auth.principal import PrincipalType
from app.core.auth.secrets import verify_secret
from app.core.auth.services import tokens as tokens_service
from app.core.auth.services.sessions import REFRESH_REUSE_GRACE
from app.core.db.models import UserAccount, UserAccountStatus, UserSession

pytestmark = pytest.mark.db

T0 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def login(session: AsyncSession, auth_settings: AuthSettings, login_client, password: str):
    """Log in through ``login_client`` at ``now``, with ``password`` unless another is given."""

    async def _login(account: UserAccount, *, now: datetime, with_password: str = password):
        assert account.email is not None
        return await issue_user_token(
            session,
            auth_settings,
            client_id=login_client.client_id,
            client_secret=login_client.client_secret,
            username=account.email,
            password=with_password,
            now=now,
        )

    return _login


@pytest.fixture
def refresh(session: AsyncSession, auth_settings: AuthSettings, login_client):
    async def _refresh(refresh_token: str, *, now: datetime):
        return await refresh_user_token(
            session,
            auth_settings,
            client_id=login_client.client_id,
            client_secret=login_client.client_secret,
            refresh_token=refresh_token,
            now=now,
        )

    return _refresh


async def _issued(result: Awaitable[IssuedUserToken | TokenDenial]) -> IssuedUserToken:
    issued = await result
    assert isinstance(issued, IssuedUserToken), issued
    return issued


async def test_the_lock_starts_at_the_threshold_doubles_per_failure_and_stops_at_the_maximum(
    make_login_account, login: Callable, session: AsyncSession
):
    account = await make_login_account()
    now = T0
    locks: list[timedelta | None] = []
    for _ in range(10):
        assert await login(account, now=now, with_password="not the password") is TokenDenial.INVALID_GRANT
        await session.refresh(account)
        locks.append(account.locked_until - now if account.locked_until else None)
        # The next attempt comes once the lock has run out: a locked account's counter does not move.
        now = (account.locked_until or now) + timedelta(seconds=1)

    minutes = [None if lock is None else lock / timedelta(minutes=1) for lock in locks]
    assert minutes == [None, None, None, None, 1, 2, 4, 8, 15, 15]
    assert account.failed_login_count == 10


async def test_a_locked_account_is_refused_with_the_right_password_until_the_lock_ends(
    make_login_account, login: Callable, session: AsyncSession
):
    account = await make_login_account()
    for _ in range(5):
        await login(account, now=T0, with_password="not the password")

    assert await login(account, now=T0 + timedelta(seconds=59)) is TokenDenial.INVALID_GRANT
    assert isinstance(await login(account, now=T0 + timedelta(minutes=1, seconds=1)), IssuedUserToken)


async def test_a_login_with_an_outdated_hash_stores_an_upgraded_one(
    make_login_account, login: Callable, session: AsyncSession, password: str
):
    account = await make_login_account()
    outdated = Argon2Hasher(time_cost=1, memory_cost=8192).hash(password)
    account.password_hash = outdated
    await session.flush()

    await _issued(login(account, now=T0))

    await session.refresh(account)
    assert account.password_hash != outdated
    assert verify_secret(password, account.password_hash)


async def test_a_rotated_out_token_within_the_grace_only_fails_and_leaves_the_session_alone(
    make_login_account, login: Callable, refresh: Callable, session: AsyncSession, audit_rows
):
    account = await make_login_account()
    first = await _issued(login(account, now=T0))
    second = await _issued(refresh(first.refresh_token, now=T0 + timedelta(minutes=1)))

    raced = await refresh(first.refresh_token, now=T0 + timedelta(minutes=1) + REFRESH_REUSE_GRACE)

    user_session = await session.scalar(select(UserSession).where(UserSession.user_account_id == account.id))
    assert raced is TokenDenial.INVALID_GRANT
    assert user_session is not None and user_session.revoked_at is None
    denied = await audit_rows(AuditEventType.TOKEN_DENIED)
    assert [(row.principal_type, row.principal_id, row.detail) for row in denied] == [
        (PrincipalType.USER, str(account.id), "refresh token reused within grace")
    ]
    assert await audit_rows(AuditEventType.SESSION_REUSE_DETECTED) == []
    assert isinstance(await refresh(second.refresh_token, now=T0 + timedelta(minutes=2)), IssuedUserToken)


async def test_a_rotated_out_token_after_the_grace_revokes_the_session(
    make_login_account, login: Callable, refresh: Callable, session: AsyncSession, audit_rows
):
    account = await make_login_account()
    first = await _issued(login(account, now=T0))
    second = await _issued(refresh(first.refresh_token, now=T0 + timedelta(minutes=1)))

    replayed = await refresh(
        first.refresh_token, now=T0 + timedelta(minutes=1) + REFRESH_REUSE_GRACE + timedelta(seconds=1)
    )

    user_session = await session.scalar(select(UserSession).where(UserSession.user_account_id == account.id))
    assert replayed is TokenDenial.INVALID_GRANT
    assert user_session is not None and user_session.revoked_reason == "reuse_detected"
    detected = await audit_rows(AuditEventType.SESSION_REUSE_DETECTED)
    assert [(row.principal_type, row.principal_id) for row in detected] == [(PrincipalType.USER, str(account.id))]
    # An old refresh token is never answered with the current one - and the current one died with the session.
    assert await refresh(second.refresh_token, now=T0 + timedelta(minutes=2)) is TokenDenial.INVALID_GRANT


async def test_a_rotated_out_token_of_a_disabled_account_revokes_the_session(
    make_login_account, login: Callable, refresh: Callable, session: AsyncSession, audit_rows
):
    account = await make_login_account()
    first = await _issued(login(account, now=T0))
    await _issued(refresh(first.refresh_token, now=T0 + timedelta(minutes=1)))
    account.status = UserAccountStatus.DISABLED
    await session.flush()

    refused = await refresh(first.refresh_token, now=T0 + timedelta(minutes=1, seconds=1))

    user_session = await session.scalar(select(UserSession).where(UserSession.user_account_id == account.id))
    assert refused is TokenDenial.INVALID_GRANT
    assert user_session is not None and user_session.revoked_reason == "account_disabled"
    assert len(await audit_rows(AuditEventType.SESSION_REVOKED)) == 1
    assert await audit_rows(AuditEventType.TOKEN_DENIED) == [], "the raced-token branch did not run first"


async def test_a_refresh_answers_the_seconds_left_until_the_session_expires(
    make_login_account, login: Callable, refresh: Callable
):
    account = await make_login_account()
    first = await _issued(login(account, now=T0))

    refreshed = await _issued(refresh(first.refresh_token, now=T0 + timedelta(days=1)))

    assert first.refresh_expires_in == 30 * 86400
    assert refreshed.refresh_expires_in == 29 * 86400


@pytest.fixture
def dummy_verifications(monkeypatch) -> list[str]:
    """Record every verification against the dummy hash the login makes."""
    calls: list[str] = []

    async def spy(password: str) -> None:
        calls.append(password)

    monkeypatch.setattr(tokens_service, "verify_dummy_password", spy)
    return calls


async def test_every_refusal_before_the_password_check_verifies_once_against_the_dummy_hash(
    make_login_account, login: Callable, session: AsyncSession, dummy_verifications: list[str], password: str
):
    disabled = await make_login_account("disabled@example.org")
    disabled.status = UserAccountStatus.DISABLED
    no_password = await make_login_account("nopassword@example.org")
    no_password.password_hash = None
    locked = await make_login_account("locked@example.org")
    locked.locked_until = T0 + timedelta(minutes=5)
    await session.flush()
    unknown = UserAccount(email="unknown@example.org")
    no_address = UserAccount(email="no address at all")

    counts = []
    for account in (unknown, disabled, no_password, locked, no_address):
        before = len(dummy_verifications)
        assert await login(account, now=T0) is TokenDenial.INVALID_GRANT
        counts.append(len(dummy_verifications) - before)

    assert counts == [1, 1, 1, 1, 1]
    assert set(dummy_verifications) == {password}


async def test_a_wrong_password_is_verified_against_the_account_not_the_dummy_hash(
    make_login_account, login: Callable, dummy_verifications: list[str]
):
    account = await make_login_account()

    assert await login(account, now=T0, with_password="not the password") is TokenDenial.INVALID_GRANT

    assert dummy_verifications == []
