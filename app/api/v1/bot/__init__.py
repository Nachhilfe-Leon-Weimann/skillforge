from fastapi import APIRouter

from app.api.v1.common import auth_error_responses

from . import runtime

router = APIRouter(
    prefix="/bot",
    tags=["bot"],
    responses=auth_error_responses(),
)
router.include_router(runtime.router)


__all__ = [
    "router",
]
