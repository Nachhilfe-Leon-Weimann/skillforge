"""Neither a one-time token, a password nor an e-mail address leaves the process (spec: security rules).

The create -> invite -> redeem flow runs with logging turned up and its JSON output captured, next to the
refusals that name an address; none of the three values may appear in that output or in an audit row.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import AuthAuditLog, UserAccount, UserActionToken
from app.core.logging import LogFormat, LoggingSettings, LogLevel, configure_logging

pytestmark = pytest.mark.db

EMAIL = "anna.schmidt@example.org"


@pytest.fixture
def restore_logging():
    """Put the logging configuration back, so the turned-up level ends with the test."""
    yield
    configure_logging(LoggingSettings())


async def test_the_create_invite_redeem_flow_logs_neither_the_token_the_password_nor_the_email(
    operator: AsyncClient, make_person, session: AsyncSession, password: str, restore_logging, capsys
):
    party, other = await make_person(), await make_person("Ben")
    # Configured inside the test: the handler writes to the stream that is current when it is set up.
    configure_logging(LoggingSettings(level=LogLevel.DEBUG, format=LogFormat.JSON))
    capsys.readouterr()

    created = await operator.post("/users", json={"party_id": str(party.id), "email": EMAIL})
    user_id = created.json()["id"]
    refused = await operator.post("/users", json={"party_id": str(other.id), "email": EMAIL.upper()})
    listed = await operator.get("/users", params={"email": EMAIL})
    token = (await operator.post(f"/users/{user_id}/invitation")).json()["token"]
    weak = await operator.post("/password/redeem", json={"token": token, "new_password": "short"})
    redeemed = await operator.post("/password/redeem", json={"token": token, "new_password": password})
    again = await operator.post("/password/redeem", json={"token": token, "new_password": password})

    output = capsys.readouterr().out
    assert [r.status_code for r in (created, refused, listed, weak, redeemed, again)] == [201, 409, 200, 422, 204, 422]
    assert output.count("http_request_") >= 6, "the flow logged its requests"
    for secret in (token, password, EMAIL, EMAIL.upper()):
        assert secret not in output

    details = [detail or "" for detail in await session.scalars(select(AuthAuditLog.detail))]
    assert details
    for secret in (token, password, EMAIL, "example.org"):
        assert not any(secret in detail for detail in details)
    assert token not in set(await session.scalars(select(UserActionToken.token_hash)))
    password_hash = await session.scalar(select(UserAccount.password_hash).where(UserAccount.party_id == party.id))
    assert password_hash is not None
    assert password not in password_hash
