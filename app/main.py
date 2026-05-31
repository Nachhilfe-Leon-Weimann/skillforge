from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from skillcore import get_project_version

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.db import Database
from app.core.db.dependencies import get_database
from app.core.logging import configure_logging, get_logger, register_request_logging

settings = get_settings()
configure_logging(settings.logging)
logger = get_logger(__name__)

app = FastAPI(
    title="skillforge",
    version=get_project_version(),
    description="Backend of the skill-platform",
)
register_request_logging(app)
app.include_router(v1_router)


@app.get("/")
async def root():
    return {"message": "Welcome to the skillforge API!"}


@app.get("/health")
async def health(database: Annotated[Database, Depends(get_database)]):
    try:
        database_ok = await database.health()
    finally:
        await database.dispose()

    if not database_ok:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "checks": {"database": "down"}},
        )

    return {"status": "ok", "checks": {"database": "ok"}}
