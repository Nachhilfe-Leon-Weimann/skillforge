"""The client grant routes against the test database: granted per mode, listed per mode, revoked per mode."""

from collections import Counter

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import GrantMode


@pytest.fixture
async def api(client: AsyncClient) -> AsyncClient:
    """The ``client`` fixture, with the client `portal` created first."""
    response = await client.post("/clients", json={"client_id": "portal", "name": "Portal"})
    assert response.status_code == 201
    return client


@pytest.mark.db
async def test_grants_are_made_listed_and_revoked_per_mode(
    api: AsyncClient, session: AsyncSession, scope_grant_details
):
    await api.post("/clients/portal/scopes", json={"scopes": ["auth:users:login"], "mode": "application"})
    await api.post("/clients/portal/scopes", json={"scopes": ["crm:read", "account:self"], "mode": "delegated"})
    granted = await api.post("/clients/portal/scopes", json={"scopes": ["crm:read"], "mode": "application"})
    revoked = await api.delete("/clients/portal/scopes/delegated/crm:read")
    session.expunge_all()  # read the client back as a new request would: with nothing loaded
    detail = await api.get("/clients/portal")

    assert granted.status_code == 200
    assert (granted.json()["application_scopes"], granted.json()["delegated_scopes"]) == (
        ["auth:users:login", "crm:read"],
        ["account:self", "crm:read"],
    )
    assert revoked.status_code == 204
    assert (detail.json()["application_scopes"], detail.json()["delegated_scopes"]) == (
        ["auth:users:login", "crm:read"],
        ["account:self"],
    )
    assert await scope_grant_details() == Counter([
        "Granted scope auth:users:login in application mode to application client portal.",
        "Granted scope account:self in delegated mode to application client portal.",
        "Granted scope crm:read in delegated mode to application client portal.",
        "Granted scope crm:read in application mode to application client portal.",
        "Removed scope crm:read in delegated mode from application client portal.",
    ])


@pytest.mark.db
async def test_a_grant_is_revoked_in_its_own_mode_only(api: AsyncClient, grants):
    await api.post("/clients/portal/scopes", json={"scopes": ["crm:read"], "mode": "delegated"})

    response = await api.delete("/clients/portal/scopes/application/crm:read")

    assert response.status_code == 404
    assert response.json()["code"] == "application_client_scope_grant_not_found"
    assert await grants() == {("crm:read", GrantMode.DELEGATED)}


@pytest.mark.db
async def test_granting_a_client_only_scope_as_delegated_is_invalid_scope_and_grants_nothing(
    api: AsyncClient, grants, scope_grant_details
):
    response = await api.post(
        "/clients/portal/scopes", json={"scopes": ["account:self", "auth:users:login"], "mode": "delegated"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid requested scope", "code": "invalid_scope"}
    assert await grants() == set()
    assert await scope_grant_details() == Counter()
