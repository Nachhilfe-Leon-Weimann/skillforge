from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import Database
from app.core.db.config import DatabaseSettings


def get_database_settings() -> DatabaseSettings:
    return get_settings().db


def get_database(settings: Annotated[DatabaseSettings, Depends(get_database_settings)]) -> Database:
    return Database.from_url(str(settings.url))


async def get_disposable_database(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[Database]:
    """Yield a request-scoped database and always dispose its engine afterwards.

    Forge currently builds a fresh engine per request; without disposal the
    engines (and their pooled connections) leak. This becomes a no-op once a
    single engine is shared via the app lifespan (#57).
    """
    try:
        yield database
    finally:
        await database.dispose()


async def get_db_session(database: Annotated[Database, Depends(get_database)]) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session

    await database.dispose()
