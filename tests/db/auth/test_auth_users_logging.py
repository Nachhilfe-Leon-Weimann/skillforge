"""A plaintext secret never leaves the process (spec: security rules).

The whole invite -> redeem flow runs with logging turned up, and neither the one-time token nor
the password may appear in the captured output or in what the flow persisted.
"""

import pytest
from sqlalchemy import select

from app.core.db.models import AuthAuditLog, UserAccount, UserActionToken
from app.core.logging import LogFormat, LoggingSettings, LogLevel, configure_logging

pytestmark = pytest.mark.db

PASSWORD = "correct horse battery staple"


@pytest.fixture
def restore_logging():
    """Put the logging configuration back, so the turned-up level ends with the test."""
    yield
    configure_logging(LoggingSettings())


async def test_the_invite_and_redeem_flow_logs_neither_the_token_nor_the_password(
    client, make_person, restore_logging, capsys
):
    party = await make_person()
    # Configured inside the test: the handler writes to the stream that is current when it is set up.
    configure_logging(LoggingSettings(level=LogLevel.DEBUG, format=LogFormat.JSON))
    capsys.readouterr()

    created = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()
    token = created["invitation"]["token"]
    redeemed = await client.post("/password/redeem", json={"token": token, "new_password": PASSWORD})

    output = capsys.readouterr().out
    assert redeemed.status_code == 204
    assert "http_request_completed" in output, "the flow logged nothing at all"
    assert token not in output
    assert PASSWORD not in output


async def test_the_flow_persists_neither_the_token_nor_the_password_in_clear(client, make_person, session):
    party = await make_person()
    created = (await client.post("/users", json={"party_id": str(party.id), "email": "anna@example.org"})).json()
    token = created["invitation"]["token"]
    await client.post("/password/redeem", json={"token": token, "new_password": PASSWORD})

    details = list(await session.scalars(select(AuthAuditLog.detail)))
    hashes = list(await session.scalars(select(UserActionToken.token_hash)))
    password_hash = await session.scalar(select(UserAccount.password_hash).where(UserAccount.party_id == party.id))

    assert details
    for detail in details:
        assert token not in (detail or "")
        assert PASSWORD not in (detail or "")
    assert token not in hashes
    assert password_hash is not None
    assert PASSWORD not in password_hash
