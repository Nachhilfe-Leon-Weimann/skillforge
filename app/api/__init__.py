from fastapi import APIRouter

from .system import system_router
from .v1.router import router as v1_router

router = APIRouter()
router.include_router(system_router)
router.include_router(v1_router)


__all__ = [
    "router",
]
