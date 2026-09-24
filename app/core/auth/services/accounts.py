"""Reading and locking user account rows - the base the other user-account services build on.

``users``, ``action_tokens`` and ``sessions`` all need an account by its ID, and ``users`` needs the
other two; with the look-ups here, none of them imports another for them.
"""

import uuid

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import UserAccount

from .errors import UserAccountNotFoundError


async def get_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccount:
    """Return the account behind ``user_id`` with its stored roles loaded, as the database says now."""
    return await _load(session, user_id, _account(UserAccount.id == user_id))


async def lock_user_account(session: AsyncSession, user_id: uuid.UUID) -> UserAccount:
    """Return the account with its row locked until the transaction ends, its stored roles loaded.

    ``populate_existing`` is what makes the lock worth taking: an account the session already holds
    would otherwise keep the attributes it was loaded with, and every check made after the lock would
    run on data from before it. The roles are read under the lock too.
    """
    return await _load(session, user_id, _account(UserAccount.id == user_id).with_for_update())


async def lock_user_account_by_email(session: AsyncSession, email: str) -> UserAccount | None:
    """Return the account that logs in with ``email`` (canonical form) with its row locked, or ``None``.

    The password login's look-up: the lock serializes two attempts on one account, so the failed-login
    counter counts both.
    """
    await session.flush()  # as in ``_load``: ``populate_existing`` would drop a pending change
    return await session.scalar(_account(UserAccount.email == email).with_for_update())


async def find_user_account_by_party(session: AsyncSession, party_id: uuid.UUID) -> UserAccount | None:
    """Return the account of the party, or ``None``: a party has at most one."""
    return await session.scalar(select(UserAccount).where(UserAccount.party_id == party_id))


def _account(condition: ColumnElement[bool]) -> Select[tuple[UserAccount]]:
    """The one statement that reads an account: its stored roles loaded, the session's copy refreshed."""
    return (
        select(UserAccount)
        .where(condition)
        .options(selectinload(UserAccount.roles))
        .execution_options(populate_existing=True)
    )


async def _load(session: AsyncSession, user_id: uuid.UUID, statement: Select[tuple[UserAccount]]) -> UserAccount:
    # ``populate_existing`` overwrites the session's copy, and the session does not autoflush: a change
    # still pending on the account or its roles would be silently lost without this flush.
    await session.flush()
    account = await session.scalar(statement)
    if account is None:
        raise UserAccountNotFoundError(f"No user account with id {user_id}")

    return account
