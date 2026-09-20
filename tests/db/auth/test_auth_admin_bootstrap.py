"""`just bootstrap-admin` creates the first admin - the one account nobody can invite (decision O)."""

from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.core.auth import AuthSettings
from app.core.auth.services.bootstrap import BOOTSTRAP_ACTOR, bootstrap_admin_account
from app.core.auth.services.errors import AccountPartyNotAPersonError, AccountPartyNotFoundError
from app.core.auth.services.users import redeem_action_token
from app.core.db.models import UserAccount, UserAccountRoleName, UserActionToken

pytestmark = pytest.mark.db

SETTINGS = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
PASSWORD = "correct horse battery staple"


async def _bootstrap(session, party_id, email: str = "admin@example.org"):
    return await bootstrap_admin_account(session, SETTINGS, party_id=party_id, email=email)


async def test_bootstrapping_creates_an_invited_admin_and_prints_nothing_but_returns_the_token(session, make_person):
    party = await make_person()

    result = await _bootstrap(session, party.id)

    assert result.created_account
    assert result.account.status.value == "invited"
    assert [role.role for role in result.account.roles] == [UserAccountRoleName.ADMIN]
    assert result.invitation is not None
    assert result.invitation.plaintext not in list(await session.scalars(select(UserActionToken.token_hash)))
    assert result.invitation.token.issued_by == BOOTSTRAP_ACTOR


async def test_bootstrapping_twice_keeps_the_account_and_issues_a_fresh_invitation(session, make_person):
    party = await make_person()
    first = await _bootstrap(session, party.id)

    second = await _bootstrap(session, party.id)

    assert not second.created_account
    assert second.account.id == first.account.id
    assert second.invitation is not None
    assert second.invitation.plaintext != first.invitation.plaintext
    assert await session.scalar(select(func.count()).select_from(UserAccount)) == 1


async def test_the_fresh_invitation_replaces_the_one_before_it(session, make_person):
    party = await make_person()
    first = await _bootstrap(session, party.id)

    await _bootstrap(session, party.id)

    live = await session.scalar(
        select(func.count())
        .select_from(UserActionToken)
        .where(UserActionToken.invalidated_at.is_(None), UserActionToken.used_at.is_(None))
    )
    assert live == 1
    assert first.invitation is not None


async def test_bootstrapping_an_account_that_has_a_password_issues_no_token(session, make_person):
    party = await make_person()
    result = await _bootstrap(session, party.id)
    assert result.invitation is not None
    await redeem_action_token(session, plaintext=result.invitation.plaintext, new_password=PASSWORD, actor="cli")

    again = await _bootstrap(session, party.id)

    assert again.invitation is None
    assert not again.created_account
    assert again.account.status.value == "active"


async def test_bootstrapping_keeps_the_admin_role_of_an_existing_account(session, make_person):
    party = await make_person()
    account = (await _bootstrap(session, party.id)).account
    account.roles.clear()
    await session.flush()

    again = await _bootstrap(session, party.id)

    assert [role.role for role in again.account.roles] == [UserAccountRoleName.ADMIN]


async def test_bootstrapping_an_unknown_party_is_refused(session):
    with pytest.raises(AccountPartyNotFoundError):
        await _bootstrap(session, party_id=uuid4())


async def test_bootstrapping_a_company_is_refused(session, make_company):
    party = await make_company()

    with pytest.raises(AccountPartyNotAPersonError):
        await _bootstrap(session, party.id)
