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


async def get_db_session(database: Annotated[Database, Depends(get_database)]) -> AsyncIterator[AsyncSession]:
    async with database.session() as session:
        yield session

    await database.dispose()
