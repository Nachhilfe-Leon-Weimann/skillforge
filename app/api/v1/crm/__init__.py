from fastapi import APIRouter

from . import companies, parties, persons, subjects

router = APIRouter(prefix="/crm", tags=["crm"])
router.include_router(parties.router)
router.include_router(persons.router)
router.include_router(companies.router)
router.include_router(subjects.router)


__all__ = [
    "router",
]
