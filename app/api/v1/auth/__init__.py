from fastapi import APIRouter

from . import token
from .token import get_issue_client_token

router = APIRouter(prefix="/auth", tags=["auth"])
router.include_router(token.router)

__all__ = [
    "get_issue_client_token",
    "router",
]
