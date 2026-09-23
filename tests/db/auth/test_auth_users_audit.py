"""What a change to a user account leaves in the audit log - and what a non-change does not."""

import pytest
from sqlalchemy import select

from app.core.db.models import AuthAuditLog

pytestmark = pytest.mark.db


async def _events(session) -> list[str]:
    return [log.event_type for log in await session.scalars(select(AuthAuditLog).order_by(AuthAuditLog.created_at))]


async def _invited(client, make_person, email: str = "anna@example.org") -> tuple[str, str]:
    party = await make_person()
    body = (await client.post("/users", json={"party_id": str(party.id), "email": email})).json()
    return body["id"], body["invitation"]["token"]


async def test_inviting_records_the_account_and_its_invitation(client, make_person, session):
    await _invited(client, make_person)

    assert await _events(session) == ["user_account.invited", "invitation.issued"]


async def test_an_empty_patch_writes_no_audit_entry(client, make_person, session):
    user_id, _ = await _invited(client, make_person)
    before = await _events(session)

    response = await client.patch(f"/users/{user_id}", json={})

    assert response.status_code == 200
    assert await _events(session) == before


async def test_a_patch_that_sets_the_current_values_writes_no_audit_entry(client, make_person, session):
    user_id, _ = await _invited(client, make_person)
    before = await _events(session)

    response = await client.patch(f"/users/{user_id}", json={"email": "Anna@Example.org"})

    assert response.status_code == 200
    assert await _events(session) == before


async def test_changing_the_email_records_the_update(client, make_person, session):
    user_id, _ = await _invited(client, make_person)

    await client.patch(f"/users/{user_id}", json={"email": "anna.neu@example.org"})

    assert (await _events(session))[-1] == "user_account.updated"


async def test_changing_the_email_and_the_status_at_once_records_both(client, make_person, session):
    user_id, token = await _invited(client, make_person)
    await client.post("/password/redeem", json={"token": token, "new_password": "correct horse battery staple"})

    await client.patch(f"/users/{user_id}", json={"email": "anna.neu@example.org", "status": "disabled"})

    assert (await _events(session))[-2:] == ["user_account.updated", "user_account.disabled"]


async def test_no_audit_detail_carries_an_email_address(client, make_person, session):
    """Decision: the audit trail says an account changed, never to what personal value."""
    user_id, _ = await _invited(client, make_person, "anna@example.org")
    await client.patch(f"/users/{user_id}", json={"email": "anna.neu@example.org"})

    details = list(await session.scalars(select(AuthAuditLog.detail)))

    assert details
    for detail in details:
        assert "@" not in (detail or "")
