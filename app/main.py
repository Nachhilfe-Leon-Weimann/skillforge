from fastapi import FastAPI

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, register_request_logging

settings = get_settings()
configure_logging(settings.logging)
logger = get_logger(__name__)

app = FastAPI(
    title="skillforge",
    version="0.1.0",
    description="Backend of the skill-platform",
)
register_request_logging(app)
app.include_router(v1_router)


@app.get("/health")
async def health():
    logger.info("health_check")
    return {"status": "ok"}
