from typing import Any

from fastapi.routing import APIRoute

ENDPOINT_SUFFIX = "_endpoint"

OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "auth",
        "description": "OAuth2 client-credentials token issuance and application client management.",
    },
    {
        "name": "bot",
        "description": (
            "SkillBot state API: runtime contexts, jobs, two-phase operations, command environments, "
            "users and authorization."
        ),
    },
    {
        "name": "crm",
        "description": "Customer relationship management: parties and the data attached to them.",
    },
    {
        "name": "system",
        "description": "Service root plus liveness and health probes for dependencies and workers.",
    },
]


def operation_id(route: APIRoute) -> str:
    """Build the operation ID ``{tag}_{function_name}`` for a route.

    The first tag is the domain (and the generated client's module). A trailing ``_endpoint`` and
    a leading ``{tag}_`` are stripped from the function name, so ``list_jobs_endpoint`` and
    ``list_jobs`` both become ``bot_list_jobs`` and ``system_health_check`` does not stutter.
    """
    if not route.tags:
        raise RuntimeError(
            f"Route {route.path!r} ({route.name}) has no tag. Set exactly one domain tag on its router: "
            "the tag prefixes the operation ID and names the generated client's module."
        )

    tag = str(route.tags[0])
    name = route.name.removesuffix(ENDPOINT_SUFFIX).removeprefix(f"{tag}_")
    return f"{tag}_{name}"
