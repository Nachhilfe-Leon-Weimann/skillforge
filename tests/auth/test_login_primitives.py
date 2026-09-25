"""The building blocks of the login: password verification with upgrade and off the event loop, the login
settings, and how a presented refresh token relates to its session."""

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from pwdlib.hashers.argon2 import Argon2Hasher
from pydantic import ValidationError

from app.core.auth import AuthSettings, passwords, secrets
from app.core.auth.secrets import digest, hash_secret, verify_and_update, verify_secret
from app.core.auth.services.sessions import REFRESH_REUSE_GRACE, RefreshTokenState, refresh_token_state
from app.core.db.models import UserSession

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def test_verify_and_update_accepts_a_current_hash_without_a_new_one():
    assert verify_and_update("correct horse", hash_secret("correct horse")) == (True, None)


def test_verify_and_update_hands_back_a_fresh_hash_for_outdated_parameters():
    outdated = Argon2Hasher(time_cost=1, memory_cost=8192).hash("correct horse")

    verified, upgraded = verify_and_update("correct horse", outdated)

    assert verified
    assert upgraded is not None and upgraded != outdated
    assert verify_secret("correct horse", upgraded)


@pytest.mark.parametrize("secret_hash", [hash_secret("another one"), "not-a-hash", "$argon2id$broken"])
def test_verify_and_update_refuses_a_wrong_password_or_a_hash_it_cannot_read(secret_hash: str):
    assert verify_and_update("correct horse", secret_hash) == (False, None)


def test_the_login_settings_default_to_the_spec():
    settings = AuthSettings.model_validate({"secret_key": "test-signing-secret-with-at-least-32-bytes"})

    assert settings.refresh_token_expire_days == 30
    assert settings.login_lockout_threshold == 5
    assert settings.login_lockout_max_minutes == 15


@pytest.mark.parametrize(
    "setting", ["refresh_token_expire_days", "login_lockout_threshold", "login_lockout_max_minutes"]
)
def test_a_login_setting_must_be_positive(setting: str):
    with pytest.raises(ValidationError):
        AuthSettings.model_validate({"secret_key": "test-signing-secret-with-at-least-32-bytes", setting: 0})


def _session(**fields: object) -> UserSession:
    return UserSession(
        refresh_token_hash=digest("current"),
        previous_refresh_token_hash=digest("previous"),
        rotated_at=NOW,
        expires_at=NOW + timedelta(days=30),
        **fields,
    )


@pytest.mark.parametrize(
    ("token", "now", "state"),
    [
        ("current", NOW, RefreshTokenState.CURRENT),
        ("previous", NOW + REFRESH_REUSE_GRACE, RefreshTokenState.RACED),
        ("previous", NOW + REFRESH_REUSE_GRACE + timedelta(microseconds=1), RefreshTokenState.REUSED),
        ("current", NOW + timedelta(days=30), RefreshTokenState.ENDED),
    ],
)
def test_a_presented_refresh_token_is_classified_against_its_session(
    token: str, now: datetime, state: RefreshTokenState
):
    assert refresh_token_state(_session(), digest(token), now=now) is state


def test_any_token_of_a_revoked_session_is_ended():
    revoked = _session(revoked_at=NOW)

    assert refresh_token_state(revoked, digest("current"), now=NOW) is RefreshTokenState.ENDED
    assert refresh_token_state(revoked, digest("previous"), now=NOW) is RefreshTokenState.ENDED


def test_the_reuse_grace_is_ten_seconds():
    assert timedelta(seconds=10) == REFRESH_REUSE_GRACE


@pytest.mark.parametrize(
    ("module", "name", "call"),
    [
        (secrets, "hash_secret", lambda: secrets.hash_secret_async("pw")),
        (secrets, "verify_secret", lambda: secrets.verify_secret_async("pw", "hash")),
        (secrets, "verify_and_update", lambda: secrets.verify_and_update_async("pw", "hash")),
        (passwords, "_verify_dummy", lambda: passwords.verify_dummy_password("pw")),
    ],
)
async def test_the_argon2_work_runs_in_a_worker_thread(monkeypatch, module, name: str, call):
    threads: list[threading.Thread] = []
    monkeypatch.setattr(module, name, lambda *args: threads.append(threading.current_thread()))

    await call()

    assert threads and threads[0] is not threading.main_thread()


async def test_the_event_loop_keeps_running_while_a_secret_is_verified(monkeypatch):
    def slow_verify(secret: str, secret_hash: str) -> bool:
        time.sleep(0.3)
        return True

    monkeypatch.setattr(secrets, "verify_secret", slow_verify)
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    assert await secrets.verify_secret_async("pw", "hash")
    ticker.cancel()

    assert ticks >= 10, "the loop served other tasks during the verification"
