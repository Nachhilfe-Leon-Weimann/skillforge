from fastapi import APIRouter

from . import auth

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
