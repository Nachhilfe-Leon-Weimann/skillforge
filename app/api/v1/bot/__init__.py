from fastapi import APIRouter

from . import authz, command_envs, jobs, operations, runtime, students, tutors, users

router = APIRouter(prefix="/bot", tags=["bot"])
router.include_router(runtime.router)
router.include_router(jobs.router)
router.include_router(operations.router)
router.include_router(command_envs.router)
router.include_router(tutors.router)
router.include_router(students.router)
router.include_router(users.router)
router.include_router(authz.router)


__all__ = [
    "router",
]
