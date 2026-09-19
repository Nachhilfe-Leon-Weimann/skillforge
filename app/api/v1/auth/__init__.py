from fastapi import APIRouter

from . import clients, me, token

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(token.router)
router.include_router(me.router)
router.include_router(clients.router)

__all__ = [
    "router",
]
