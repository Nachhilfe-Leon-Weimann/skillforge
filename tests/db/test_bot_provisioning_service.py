import uuid

import pytest

from app.core.db.models import DiscordAccount, DiscordUser, MemberRole, Party, PartyType
from app.services.bot import (
    PartyNotFoundError,
    get_principal_view,
    link_discord_account,
    load_party_for_discord_id,
    upsert_discord_user,
)

# Snowflake-sized ids (> 2**32) so the tests fail if discord_id is not a BIGINT.
DISCORD_ID = 123456789012345678
OTHER_ID = 987654321098765432


# --- upsert user ------------------------------------------------------------


@pytest.mark.db
async def test_upsert_discord_user_creates_then_updates(session):
    created = await upsert_discord_user(session, discord_id=DISCORD_ID, role=MemberRole.STUDENT, nick_name="Stud")
    assert created.role is MemberRole.STUDENT
    assert created.active is True

    updated = await upsert_discord_user(
        session, discord_id=DISCORD_ID, role=MemberRole.TUTOR, nick_name="Now Tutor", active=False
    )
    assert updated.discord_id == DISCORD_ID
    assert updated.role is MemberRole.TUTOR
    assert updated.nick_name == "Now Tutor"
    assert updated.active is False

    fetched = await session.get(DiscordUser, DISCORD_ID)
    assert fetched is not None
    assert fetched.role is MemberRole.TUTOR
    assert fetched.nick_name == "Now Tutor"
    assert fetched.active is False


# --- link account -----------------------------------------------------------


@pytest.mark.db
async def test_link_discord_account_requires_existing_party(session):
    with pytest.raises(PartyNotFoundError):
        await link_discord_account(session, discord_id=DISCORD_ID, party_id=uuid.uuid4())


@pytest.mark.db
async def test_link_discord_account_links_to_existing_party(session):
    party = await _add_party(session)
    await upsert_discord_user(session, discord_id=DISCORD_ID, role=MemberRole.STUDENT, nick_name="Stud")

    account = await link_discord_account(
        session, discord_id=DISCORD_ID, party_id=party.id, is_primary=True, active=True
    )
    assert account.party_id == party.id
    assert account.is_primary is True

    # Visible through the existing principal view + profile loader.
    view = await get_principal_view(session, DISCORD_ID)
    assert view.party is not None
    assert view.party.id == party.id
    loaded = await load_party_for_discord_id(session, DISCORD_ID)
    assert loaded is not None
    assert loaded.id == party.id


@pytest.mark.db
async def test_link_primary_demotes_existing_primary(session):
    party = await _add_party(session)

    first = await link_discord_account(session, discord_id=DISCORD_ID, party_id=party.id, is_primary=True)
    assert first.is_primary is True

    second = await link_discord_account(session, discord_id=OTHER_ID, party_id=party.id, is_primary=True)
    assert second.is_primary is True

    demoted = await session.get(DiscordAccount, DISCORD_ID)
    assert demoted is not None
    assert demoted.is_primary is False


@pytest.mark.db
async def test_relink_same_account_is_idempotent(session):
    party = await _add_party(session)

    await link_discord_account(session, discord_id=DISCORD_ID, party_id=party.id, is_primary=True)
    again = await link_discord_account(session, discord_id=DISCORD_ID, party_id=party.id, is_primary=True)

    assert again.party_id == party.id
    assert again.is_primary is True


# --- helpers ----------------------------------------------------------------


async def _add_party(session) -> Party:
    party = Party(type=PartyType.PERSON)
    session.add(party)
    await session.flush()
    return party
