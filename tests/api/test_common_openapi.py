import pytest
from fastapi import APIRouter, FastAPI

from app.api.v1.common.openapi import operation_id


def _operation_ids(app: FastAPI) -> set[str]:
    return {operation["operationId"] for item in app.openapi()["paths"].values() for operation in item.values()}


def test_operation_id_joins_the_first_tag_and_the_function_name():
    app = FastAPI(generate_unique_id_function=operation_id)

    @app.get("/jobs", tags=["bot"])
    async def list_jobs() -> None: ...

    assert _operation_ids(app) == {"bot_list_jobs"}


def test_operation_id_ignores_a_trailing_endpoint_suffix():
    app = FastAPI(generate_unique_id_function=operation_id)

    @app.get("/jobs", tags=["bot"])
    async def list_jobs_endpoint() -> None: ...

    assert _operation_ids(app) == {"bot_list_jobs"}


def test_operation_id_does_not_stutter_when_the_function_name_starts_with_the_tag():
    app = FastAPI(generate_unique_id_function=operation_id)

    @app.get("/health", tags=["system"])
    async def system_health_check() -> None: ...

    assert _operation_ids(app) == {"system_health_check"}


def test_operation_id_uses_the_tags_merged_from_nested_routers():
    leaf = APIRouter(prefix="/jobs")

    @leaf.get("")
    async def list_jobs_endpoint() -> None: ...

    domain = APIRouter(prefix="/bot", tags=["bot"])
    domain.include_router(leaf)
    version = APIRouter(prefix="/api/v1")
    version.include_router(domain)
    app = FastAPI(generate_unique_id_function=operation_id)
    app.include_router(version)

    assert _operation_ids(app) == {"bot_list_jobs"}


def test_untagged_route_is_rejected_at_registration():
    app = FastAPI(generate_unique_id_function=operation_id)

    with pytest.raises(RuntimeError, match="/untagged"):

        @app.get("/untagged")
        async def untagged() -> None: ...
