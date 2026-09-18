"""Dependencies shared by all `/api/v1` domains."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.dependencies import get_db_session

# ``scope="function"`` ends the session - and thereby commits - before the response is sent. With
# the default scope the commit runs afterwards, so a failing commit would still answer 2xx.
DBSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]
