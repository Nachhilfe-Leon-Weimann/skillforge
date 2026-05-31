from fastapi import APIRouter, Depends

from app.api.v1.common import auth_error_responses
from app.core.auth import Scope, require_scopes

from . import users

router = APIRouter(
    prefix="/bot",
    tags=["bot"],
    dependencies=[
        Depends(require_scopes(Scope.BOT_READ)),
    ],
    responses=auth_error_responses(Scope.BOT_READ),
)
router.include_router(users.router)


__all__ = [
    "router",
]
