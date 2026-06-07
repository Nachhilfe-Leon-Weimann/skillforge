from fastapi import APIRouter

from .health import router as health_router

system_router = APIRouter(tags=["system"])
system_router.include_router(health_router)


__all__ = [
    "system_router",
]
