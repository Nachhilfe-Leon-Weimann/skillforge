"""A failing statement must not carry its bound parameters into the log.

``hide_parameters=True`` removes SQLAlchemy's ``[parameters: ...]`` block from the exception text.
It cannot remove what the server says itself: for a unique violation Postgres' ``DETAIL`` repeats
the offending key. The CRM services therefore translate uniqueness violations inside a SAVEPOINT
into domain errors, which are never logged; only an untranslated ``IntegrityError`` would still
show the key.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.db


async def test_a_database_error_does_not_repeat_the_bound_parameters(session: AsyncSession):
    with pytest.raises(DBAPIError) as raised:
        async with session.begin_nested():
            await session.execute(text("SELECT CAST(:value AS integer) / 0"), {"value": 73190042})

    message = str(raised.value)
    assert "73190042" not in message
    assert "[parameters:" not in message
    assert "hidden due to hide_parameters" in message
