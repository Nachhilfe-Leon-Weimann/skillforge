from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database


def get_database(request: Request) -> Database:
    """Return the shared engine created once at startup (see app lifespan, #57).

    The engine lives on ``app.state`` for the app's lifetime; request handlers
    borrow it and must never dispose it.
    """
    database: Database = request.app.state.database
    return database


async def get_db_session(database: Annotated[Database, Depends(get_database)]) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session


# The one request session: every dependency that takes it - an endpoint, an auth guard - shares it.
# ``scope="function"`` ends the session - and thereby commits - before the response is sent. With
# the default scope the commit runs afterwards, so a failing commit would still answer 2xx.
DBSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]
