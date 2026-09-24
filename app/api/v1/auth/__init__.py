from fastapi import APIRouter

from . import clients, me, password, revoke, token, users

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(token.router)
router.include_router(me.router)
router.include_router(clients.router)
router.include_router(users.router)
router.include_router(password.router)
router.include_router(revoke.router)

__all__ = [
    "router",
]
