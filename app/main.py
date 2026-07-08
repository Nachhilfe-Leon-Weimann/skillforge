from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from skillcore import get_project_version

from app.api import router
from app.core.config import get_settings
from app.core.db import Database
from app.core.logging import configure_logging, get_logger, register_request_logging

settings = get_settings()
configure_logging(settings.logging)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own a single database engine for the whole app lifetime.

    The old per-request ``get_database`` built a fresh engine on every call,
    paying cold pool setup each time and leaking engines on the health path.
    We now create one shared ``Database`` at startup, expose it on
    ``app.state``, and dispose it exactly once on shutdown. See #57.
    """
    database = Database.from_url(str(settings.db.url))
    app.state.database = database
    logger.info("db_engine_created")
    try:
        yield
    finally:
        await database.dispose()
        logger.info("db_engine_disposed")


app = FastAPI(
    title="skillforge",
    version=get_project_version(),
    description="Backend of the skill-platform",
    lifespan=lifespan,
)
register_request_logging(app)
app.include_router(router)


@app.get("/")
async def root():
    return {"message": "Welcome to the skillforge API!"}
