"""The party graph the bot loads for a Discord ID, and what the operational profile shows of it."""

import warnings

import pytest
from sqlalchemy.exc import SAWarning

from app.api.v1.bot.schemas import OperationalProfile
from app.core.db.models import DiscordAccount, Party, PartyType, Person
from app.services.bot.profile import load_parties_for_discord_ids

PRIMARY_ID = 900000000000000002
SECONDARY_ID = 900000000000000001


@pytest.mark.db
async def test_profile_lists_every_discord_account_of_the_party_primary_first(session):
    party = Party(type=PartyType.PERSON)
    session.add_all([
        party,
        Person(firstname="Max", lastname="Muster", party=party),
        DiscordAccount(discord_id=SECONDARY_ID, party=party, is_primary=False),
        DiscordAccount(discord_id=PRIMARY_ID, party=party, is_primary=True),
    ])
    await session.flush()
    # The loader must fill the collection, not the identity map that still knows the insert order.
    session.expunge_all()

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        parties = await load_parties_for_discord_ids(session, [SECONDARY_ID])

    profile = OperationalProfile.from_party(parties[SECONDARY_ID])
    assert [(account.discord_id, account.is_primary) for account in profile.external_accounts.discord] == [
        (PRIMARY_ID, True),
        (SECONDARY_ID, False),
    ]
