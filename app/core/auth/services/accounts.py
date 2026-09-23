"""Reading and locking user account rows - the base the user-account services build on.

``users``, ``action_tokens`` and ``sessions`` all need an account by its ID, and ``users`` needs
the other two. With the look-ups here, each of them imports this module instead of another.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import UserAccount

from .errors import UserAccountNotFoundError


async def get_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccount:
    """Return the account behind ``user_id``, with its stored roles loaded.

    A ``select`` rather than ``session.get``: the latter answers from the identity map without
    applying the loader option, and the unloaded collection would then be read after the response
    has left the session.
    """
    account = await session.scalar(
        select(UserAccount).where(UserAccount.id == user_id).options(selectinload(UserAccount.roles))
    )
    if account is None:
        raise UserAccountNotFoundError(f"No user account with id {user_id}")

    return account


async def lock_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccount:
    """Return the account with its row locked until the transaction ends, its stored roles loaded.

    ``populate_existing`` is what makes the lock worth taking: an account the session already
    holds would otherwise keep the attributes it was loaded with, and every check made after the
    lock would run on data from before it. The roles are read once the lock is held, so a caller
    that changes them sees what the previous holder left.
    """
    account = await session.scalar(
        select(UserAccount)
        .where(UserAccount.id == user_id)
        .options(selectinload(UserAccount.roles))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise UserAccountNotFoundError(f"No user account with id {user_id}")

    return account


async def find_user_account_by_party(session: AsyncSession, party_id: uuid.UUID) -> UserAccount | None:
    """Return the account of the party, or ``None``: a party has at most one (decision C)."""
    return await session.scalar(select(UserAccount).where(UserAccount.party_id == party_id))
