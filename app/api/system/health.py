from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.db import Database
from app.core.db.dependencies import get_database
from app.services.system import (
    DependenciesHealthCheckResponse,
    DependencyHealthCheckResponse,
    DependencyName,
    HealthCheckResponse,
    HealthStatus,
    SystemHealthCheckResponse,
    WorkerHealthCheckResponse,
    WorkerName,
    WorkersHealthCheckResponse,
    check_dependencies_health,
    check_dependency_health,
    check_system_health,
    check_worker_health,
    check_workers_health,
)

router = APIRouter(prefix="/health")

DatabaseDep = Annotated[Database, Depends(get_database)]


def _http_status_for(health: HealthStatus) -> int:
    match health:
        case HealthStatus.HEALTHY:
            return status.HTTP_200_OK
        case HealthStatus.UNHEALTHY:
            return status.HTTP_503_SERVICE_UNAVAILABLE
        case HealthStatus.ERROR:
            return status.HTTP_500_INTERNAL_SERVER_ERROR


@router.get("")
async def system_health_check(response: Response, database: DatabaseDep) -> SystemHealthCheckResponse:
    """Aggregate health across all dependencies and workers."""
    result = await check_system_health(database)
    response.status_code = _http_status_for(result.status)
    return result


@router.get("/live")
async def liveness_check() -> HealthCheckResponse:
    """Liveness probe: the process is up and serving requests."""
    return HealthCheckResponse(status=HealthStatus.HEALTHY)


@router.get("/dependencies")
async def dependencies_health_check(response: Response, database: DatabaseDep) -> DependenciesHealthCheckResponse:
    """Aggregate health across all external dependencies."""
    result = await check_dependencies_health(database)
    response.status_code = _http_status_for(result.status)
    return result


@router.get("/dependencies/{dependency_name}")
async def dependency_health_check(
    dependency_name: DependencyName, response: Response, database: DatabaseDep
) -> DependencyHealthCheckResponse:
    """Health of a single dependency."""
    result = await check_dependency_health(dependency_name, database)
    response.status_code = _http_status_for(result.status)
    return result


@router.get("/workers")
async def workers_health_check(response: Response, database: DatabaseDep) -> WorkersHealthCheckResponse:
    """Aggregate health across all background workers."""
    result = await check_workers_health(database)
    response.status_code = _http_status_for(result.status)
    return result


@router.get("/workers/{worker_name}")
async def worker_health_check(
    worker_name: WorkerName, response: Response, database: DatabaseDep
) -> WorkerHealthCheckResponse:
    """Health of a single background worker."""
    result = await check_worker_health(worker_name, database)
    response.status_code = _http_status_for(result.status)
    return result
