from fastapi import APIRouter

from . import companies, contact_infos, parties, persons, roles, subjects

router = APIRouter(prefix="/crm", tags=["crm"])
router.include_router(parties.router)
router.include_router(persons.router)
router.include_router(roles.router)
router.include_router(contact_infos.router)
router.include_router(companies.router)
router.include_router(subjects.router)


__all__ = [
    "router",
]
