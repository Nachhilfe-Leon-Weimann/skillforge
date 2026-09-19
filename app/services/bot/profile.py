from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import DiscordAccount, Party
from app.services.crm.parties import PARTY_GRAPH

# What the operational profile touches beyond the CRM's party graph.
_PROFILE_EXTRAS = (
    selectinload(Party.outgoing_relations),
    selectinload(Party.incoming_relations),
    selectinload(Party.discord_accounts),
    selectinload(Party.microsoft_account),
)


async def load_party_for_discord_id(session: AsyncSession, discord_id: int) -> Party | None:
    parties = await load_parties_for_discord_ids(session, [discord_id])
    return parties.get(discord_id)


async def load_parties_for_discord_ids(session: AsyncSession, discord_ids: Iterable[int]) -> dict[int, Party]:
    """Load the parties linked to ``discord_ids`` with the full operational profile graph eager-loaded.

    Returns a party per id that has a linked account; ids without one are simply absent. Everything
    ``OperationalProfile.from_party`` touches must be loaded here: async sessions cannot lazy-load, so a
    missing relation would raise at serialization time. The party itself comes through the CRM's one
    loading path (``PARTY_GRAPH``, ADR 0007); only the extras are the bot's own.
    """

    ids = list(dict.fromkeys(discord_ids))
    if not ids:
        return {}

    result = await session.execute(
        select(DiscordAccount.discord_id, Party)
        .join(DiscordAccount, DiscordAccount.party_id == Party.id)
        .where(DiscordAccount.discord_id.in_(ids))
        .options(*PARTY_GRAPH, *_PROFILE_EXTRAS)
    )
    return {discord_id: party for discord_id, party in result.all()}
