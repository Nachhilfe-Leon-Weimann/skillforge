"""Two overlapping issues of the same purpose must not leave two live tokens.

This needs two real transactions, so it cannot run on the rolled-back ``session`` fixture: it
commits, and removes what it created.
"""

import asyncio

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select

from app.core.auth import AuthSettings
from app.core.auth.services.users import invite_user_account, issue_action_token
from app.core.db import Database
from app.core.db.models import AuthAuditLog, UserActionToken, UserActionTokenPurpose
from app.services.crm import parties, persons

pytestmark = pytest.mark.db

SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))


async def test_an_overlapping_issue_waits_and_leaves_exactly_one_live_token(db: Database):
    async with db.session() as setup:
        party_id = (await persons.create_person(setup, firstname="Race", lastname="Condition")).id
        view, _ = await invite_user_account(
            setup,
            SETTINGS,
            party_id=party_id,
            email="race.condition@example.org",
            actor="cli",
        )
        user_id = view.account.id

    async def issue(session):
        return await issue_action_token(
            session,
            SETTINGS,
            user_id=user_id,
            purpose=UserActionTokenPurpose.INVITATION,
            actor="cli",
        )

    first, second = db.session_factory(), db.session_factory()
    try:
        # The first request has invalidated what it found and inserted its token, but not
        # committed yet ...
        await issue(first)
        # ... when the second arrives. Without the row lock it would invalidate the same tokens the
        # first one saw and add a second live one next to it.
        retry = asyncio.create_task(issue(second))
        await asyncio.sleep(0.5)
        assert not retry.done(), "the second request waits for the first"

        await first.commit()
        await asyncio.wait_for(retry, timeout=10)
        await second.commit()

        async with db.session(write=False) as check:
            live = await check.scalar(
                select(func.count())
                .select_from(UserActionToken)
                .where(
                    UserActionToken.user_account_id == user_id,
                    UserActionToken.used_at.is_(None),
                    UserActionToken.invalidated_at.is_(None),
                )
            )
        assert live == 1
    finally:
        await first.close()
        await second.close()
        async with db.session() as cleanup:
            await parties.delete_party(cleanup, party_id)
            # The account cascades with the party, the audit log deliberately does not (decision M).
            # It is committed, so this test has to take its own entries back out.
            await cleanup.execute(delete(AuthAuditLog).where(AuthAuditLog.principal_id == str(user_id)))
