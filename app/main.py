from fastapi import FastAPI
from skillcore import get_project_version

from app.api import router
from app.core.config import get_settings
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
app.include_router(router)


@app.get("/")
async def root():
    return {"message": "Welcome to the skillforge API!"}
