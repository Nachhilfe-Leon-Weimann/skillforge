from fastapi import APIRouter

from . import auth, bot, crm

router = APIRouter(prefix="/api/v1")
router.include_router(auth.router)
router.include_router(bot.router)
router.include_router(crm.router)
