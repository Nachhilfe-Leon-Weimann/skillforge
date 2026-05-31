from fastapi import APIRouter, Depends

from app.core.auth import Scope, require_scopes

from . import users

router = APIRouter(
    prefix="/bot",
    tags=["bot"],
    dependencies=[
        Depends(require_scopes(Scope.BOT_READ)),
    ],
)
router.include_router(users.router)


__all__ = [
    "router",
]
