"""The one security scheme of the contract: its key, its flows and what it guards.

P0-4 of `docs/specs/user-authentication.md` renamed the scheme key from the class name FastAPI
derived (`OAuth2ClientCredentialsBearer`) to `OAuth2`, deliberately and once, while no consumer is
live. `SECURITY_REQUIREMENTS_AT_THE_RENAME` is the scopes every operation demanded before that
commit: the rename had to touch the key and nothing else.
"""

from typing import Any

import pytest

from app.core.auth import Scope
from app.main import app

HTTP_METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}
SCHEME_NAME = "OAuth2"
TOKEN_URL = "/api/v1/auth/token"

SECURITY_REQUIREMENTS_AT_THE_RENAME: dict[str, list[str]] = {
    "DELETE /api/v1/auth/clients/{client_id}/scopes/{scope_key}": ["auth:clients:manage"],
    "DELETE /api/v1/auth/clients/{client_id}/secrets/{secret_id}": ["auth:clients:manage"],
    "DELETE /api/v1/bot/command-envs/{guild_id}/{channel_id}/{kind}": ["bot:write"],
    "DELETE /api/v1/bot/users/{discord_id}/account": ["bot:write"],
    "DELETE /api/v1/bot/users/{discord_id}/groups/{group_key}": ["bot:write"],
    "DELETE /api/v1/crm/parties/{party_id}": ["crm:write"],
    "DELETE /api/v1/crm/parties/{party_id}/contact-infos/{contact_info_id}": ["crm:write"],
    "DELETE /api/v1/crm/parties/{party_id}/relations/{type}/{to_party_id}": ["crm:write"],
    "DELETE /api/v1/crm/persons/{party_id}/student": ["crm:write"],
    "DELETE /api/v1/crm/persons/{party_id}/tutor": ["crm:write"],
    "DELETE /api/v1/crm/subjects/{subject_id}": ["crm:write"],
    "GET /api/v1/auth/clients": ["auth:clients:manage"],
    "GET /api/v1/auth/clients/{client_id}": ["auth:clients:manage"],
    "GET /api/v1/auth/me": [],
    "GET /api/v1/bot/jobs": ["bot:read"],
    "GET /api/v1/bot/jobs/summary": ["bot:read"],
    "GET /api/v1/bot/jobs/{job_id}": ["bot:read"],
    "GET /api/v1/bot/operations": ["bot:read"],
    "GET /api/v1/bot/operations/{operation_id}": ["bot:read"],
    "GET /api/v1/bot/runtime/command-envs/resolve": ["bot:read"],
    "GET /api/v1/bot/runtime/principals/{discord_id}": ["bot:read"],
    "GET /api/v1/bot/runtime/students/{guild_id}/{discord_id}": ["bot:read"],
    "GET /api/v1/bot/runtime/tutors/{guild_id}/{discord_id}": ["bot:read"],
    "GET /api/v1/crm/parties": ["crm:read"],
    "GET /api/v1/crm/parties/{party_id}": ["crm:read"],
    "GET /api/v1/crm/parties/{party_id}/relations": ["crm:read"],
    "GET /api/v1/crm/subjects": ["crm:read"],
    "PATCH /api/v1/auth/clients/{client_id}": ["auth:clients:manage"],
    "PATCH /api/v1/crm/companies/{party_id}": ["crm:write"],
    "PATCH /api/v1/crm/parties/{party_id}/contact-infos/{contact_info_id}": ["crm:write"],
    "PATCH /api/v1/crm/persons/{party_id}": ["crm:write"],
    "PATCH /api/v1/crm/subjects/{subject_id}": ["crm:write"],
    "POST /api/v1/auth/clients": ["auth:clients:manage"],
    "POST /api/v1/auth/clients/{client_id}/scopes": ["auth:clients:manage"],
    "POST /api/v1/auth/clients/{client_id}/secrets": ["auth:clients:manage"],
    "POST /api/v1/bot/authz/check": ["bot:read"],
    "POST /api/v1/bot/jobs/claim": ["bot:write"],
    "POST /api/v1/bot/jobs/{job_id}/complete": ["bot:write"],
    "POST /api/v1/bot/jobs/{job_id}/fail": ["bot:write"],
    "POST /api/v1/bot/operations/{operation_id}/cancel": ["bot:write"],
    "POST /api/v1/bot/runtime/principals/batch": ["bot:read"],
    "POST /api/v1/bot/runtime/students/{guild_id}/batch": ["bot:read"],
    "POST /api/v1/bot/runtime/tutors/{guild_id}/batch": ["bot:read"],
    "POST /api/v1/bot/students/activations/prepare": ["bot:write"],
    "POST /api/v1/bot/students/activations/{operation_id}/commit": ["bot:write"],
    "POST /api/v1/bot/students/{guild_id}/{student_discord_id}/deactivate/prepare": ["bot:write"],
    "POST /api/v1/bot/students/{guild_id}/{student_discord_id}/deactivate/{operation_id}/commit": ["bot:write"],
    "POST /api/v1/bot/students/{guild_id}/{student_discord_id}/pop/prepare": ["bot:write"],
    "POST /api/v1/bot/students/{guild_id}/{student_discord_id}/pop/{operation_id}/commit": ["bot:write"],
    "POST /api/v1/bot/students/{guild_id}/{student_discord_id}/stash/prepare": ["bot:write"],
    "POST /api/v1/bot/students/{guild_id}/{student_discord_id}/stash/{operation_id}/commit": ["bot:write"],
    "POST /api/v1/bot/tutors/activations/prepare": ["bot:write"],
    "POST /api/v1/bot/tutors/activations/{operation_id}/commit": ["bot:write"],
    "POST /api/v1/bot/tutors/{guild_id}/{tutor_discord_id}/deactivate/prepare": ["bot:write"],
    "POST /api/v1/bot/tutors/{guild_id}/{tutor_discord_id}/deactivate/{operation_id}/commit": ["bot:write"],
    "POST /api/v1/crm/companies": ["crm:write"],
    "POST /api/v1/crm/parties/{party_id}/contact-infos": ["crm:write"],
    "POST /api/v1/crm/persons": ["crm:write"],
    "POST /api/v1/crm/subjects": ["crm:write"],
    "PUT /api/v1/bot/command-envs": ["bot:write"],
    "PUT /api/v1/bot/users/{discord_id}": ["bot:write"],
    "PUT /api/v1/bot/users/{discord_id}/account": ["bot:write"],
    "PUT /api/v1/bot/users/{discord_id}/groups/{group_key}": ["bot:write"],
    "PUT /api/v1/crm/parties/{party_id}/relations/{type}/{to_party_id}": ["crm:write"],
    "PUT /api/v1/crm/persons/{party_id}/student": ["crm:write"],
    "PUT /api/v1/crm/persons/{party_id}/tutor": ["crm:write"],
}


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return app.openapi()


def test_the_contract_declares_exactly_one_security_scheme(schema: dict[str, Any]):
    assert set(schema["components"]["securitySchemes"]) == {SCHEME_NAME}


def test_the_scheme_offers_the_client_credentials_and_the_password_flow(schema: dict[str, Any]):
    """Both grants of the token endpoint, so Swagger UI's Authorize dialog can log a user in."""
    flows = schema["components"]["securitySchemes"][SCHEME_NAME]["flows"]
    every_scope = {scope.value: scope.description for scope in Scope}

    assert set(flows) == {"clientCredentials", "password"}
    for flow in flows.values():
        assert flow["tokenUrl"] == TOKEN_URL
        assert flow["scopes"] == every_scope


def test_every_secured_operation_references_only_that_scheme(schema: dict[str, Any]):
    secured = [(method, path, operation["security"]) for method, path, operation in _operations(schema)]

    assert secured
    for method, path, security in secured:
        for requirement in security:
            assert set(requirement) == {SCHEME_NAME}, f"{method} {path}"


def test_the_scheme_rename_left_every_security_requirement_unchanged(schema: dict[str, Any]):
    """Only the key moved: each operation that existed at the rename still demands the same scopes.

    Operations added afterwards are not pinned here - they were never part of the renamed set.
    """
    requirements = {
        f"{method} {path}": [scope for requirement in security for scope in requirement[SCHEME_NAME]]
        for method, path, security in ((m, p, o["security"]) for m, p, o in _operations(schema))
    }

    for operation, scopes in SECURITY_REQUIREMENTS_AT_THE_RENAME.items():
        assert requirements.get(operation) == scopes, operation


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Every operation that declares a security requirement."""
    return [
        (method.upper(), path, operation)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if method in HTTP_METHODS and operation.get("security")
    ]
