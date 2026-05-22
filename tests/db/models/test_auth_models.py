import pytest


@pytest.mark.db
async def test_auth_model_metadata():
    from app.core.db.models import (
        ApplicationClient,
        ApplicationClientSecret,
        ApplicationClientStatus,
        AuthAuditLog,
        PermissionScope,
    )

    assert ApplicationClient.__table__.schema == "auth"
    assert ApplicationClientSecret.__table__.schema == "auth"
    assert AuthAuditLog.__table__.schema == "auth"
    assert PermissionScope.__table__.schema == "auth"
    assert getattr(ApplicationClient.__table__.c.status.type, "enums", None) == ["active", "disabled"]
    assert ApplicationClient.__table__.c.status.default.arg == ApplicationClientStatus.ACTIVE
    assert PermissionScope.__table__.c.active.server_default is not None


@pytest.mark.db
async def test_application_client_secret_relationship(session):
    from app.core.db.models import ApplicationClient, ApplicationClientSecret

    client = ApplicationClient(client_id="some-client", name="SomeClient")
    secret = ApplicationClientSecret(
        application_client=client,
        secret_hash="hashed-secret",
        label="default",
    )

    session.add(client)
    await session.flush()

    assert secret.application_client_id == client.id
    assert client.secrets == [secret]


@pytest.mark.db
async def test_application_client_scope_grant_relationship(session):
    from app.core.db.models import ApplicationClient, ApplicationClientScopeGrant, PermissionScope

    client = ApplicationClient(client_id="some-client", name="SomeClient")
    scope = PermissionScope(key="data:read", description="Read some clients data API")
    grant = ApplicationClientScopeGrant(application_client=client, permission_scope=scope)

    session.add_all([client, scope, grant])
    await session.flush()

    assert grant.application_client_id == client.id
    assert grant.scope_key == scope.key
    assert client.scope_grants == [grant]
    assert scope.client_scope_grants == [grant]
    assert scope.active is True


@pytest.mark.db
async def test_auth_audit_log(session):
    import uuid

    from app.core.db.models import AuthAuditLog

    log = AuthAuditLog(
        principal_type="application",
        principal_id="some-client",
        event_type="token.issue",
        success=True,
        detail="issued client credentials token",
    )

    session.add(log)
    await session.flush()

    assert isinstance(log.id, uuid.UUID)
