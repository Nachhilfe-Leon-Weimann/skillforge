"""What two overlapping requests on the same account do to each other.

These need two real transactions, so they cannot run on the rolled-back ``session`` fixture: they
commit, and remove what they created - including their audit entries, which deliberately do not
cascade with the party (decision M).
"""

import asyncio
import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings
from app.core.auth.secrets import verify_secret
from app.core.auth.services.errors import InvalidActionTokenError, UserRoleNotFoundError
from app.core.auth.services.users import invite_user_account, redeem_action_token, remove_user_role
from app.core.db import Database
from app.core.db.models import AuthAuditLog, UserAccount, UserAccountRoleName
from app.services.crm import parties, persons

pytestmark = pytest.mark.db

SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
FIRST_PASSWORD = "the first password wins"
SECOND_PASSWORD = "the second password loses"


async def _invited(db: Database, *, email: str, roles=()) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Create a committed person with a user account; returns ``(party_id, user_id, token)``."""
    async with db.session() as setup:
        party_id = (await persons.create_person(setup, firstname="Race", lastname="Condition")).id
        view, invitation = await invite_user_account(
            setup, SETTINGS, party_id=party_id, email=email, roles=roles, actor="cli"
        )
        return party_id, view.account.id, invitation.plaintext


async def _clean_up(db: Database, party_id: uuid.UUID, user_id: uuid.UUID) -> None:
    async with db.session() as cleanup:
        await parties.delete_party(cleanup, party_id)
        await cleanup.execute(delete(AuthAuditLog).where(AuthAuditLog.principal_id == str(user_id)))


async def test_an_overlapping_redeem_of_the_same_token_is_refused(db: Database):
    """The token is one-time: the check has to be made under the lock, not before it."""
    party_id, user_id, token = await _invited(db, email="race.redeem@example.org")

    first: AsyncSession = db.session_factory()
    second: AsyncSession = db.session_factory()
    try:
        # The first redeem holds the account row ...
        await redeem_action_token(first, plaintext=token, new_password=FIRST_PASSWORD, actor="cli")
        # ... when the second arrives with the same token. It read the token before the first
        # committed, so only a re-read under the lock can see that it has been used.
        retry = asyncio.create_task(
            redeem_action_token(second, plaintext=token, new_password=SECOND_PASSWORD, actor="cli")
        )
        await asyncio.sleep(0.5)
        assert not retry.done(), "the second redeem waits for the first"

        await first.commit()
        with pytest.raises(InvalidActionTokenError):
            await asyncio.wait_for(retry, timeout=10)
        await second.rollback()

        async with db.session(write=False) as check:
            password_hash = await check.scalar(select(UserAccount.password_hash).where(UserAccount.id == user_id))
        assert password_hash is not None
        assert verify_secret(FIRST_PASSWORD, password_hash)
        assert not verify_secret(SECOND_PASSWORD, password_hash)
    finally:
        await first.close()
        await second.close()
        await _clean_up(db, party_id, user_id)


async def test_an_overlapping_removal_of_the_same_role_is_not_found(db: Database):
    """Without the row lock the second removal deletes a row that is already gone, and says nothing."""
    party_id, user_id, _ = await _invited(db, email="race.role@example.org", roles=[UserAccountRoleName.ADMIN])

    first: AsyncSession = db.session_factory()
    second: AsyncSession = db.session_factory()
    try:
        await remove_user_role(first, user_id, role=UserAccountRoleName.ADMIN, actor="cli")
        retry = asyncio.create_task(remove_user_role(second, user_id, role=UserAccountRoleName.ADMIN, actor="cli"))
        await asyncio.sleep(0.5)
        assert not retry.done(), "the second removal waits for the first"

        await first.commit()
        with pytest.raises(UserRoleNotFoundError):
            await asyncio.wait_for(retry, timeout=10)
        await second.rollback()
    finally:
        await first.close()
        await second.close()
        await _clean_up(db, party_id, user_id)
