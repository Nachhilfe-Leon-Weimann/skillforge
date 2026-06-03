from fastapi import APIRouter

from app.api.v1.common import auth_error_responses

from . import command_envs, jobs, runtime, students, tutors

router = APIRouter(
    prefix="/bot",
    tags=["bot"],
    responses=auth_error_responses(),
)
router.include_router(runtime.router)
router.include_router(jobs.router)
router.include_router(command_envs.router)
router.include_router(tutors.router)
router.include_router(students.router)


__all__ = [
    "router",
]
