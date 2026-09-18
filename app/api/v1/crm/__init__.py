from fastapi import APIRouter

from . import subjects

router = APIRouter(prefix="/crm", tags=["crm"])
router.include_router(subjects.router)


__all__ = [
    "router",
]
