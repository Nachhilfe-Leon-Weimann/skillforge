from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.auth import (
    AuthSettings,
    InvalidClientCredentialsError,
    InvalidClientScopeError,
    create_client_secret,
    issue_client_token,
    validate_access_token,
)
from app.core.db.models import (
    ApplicationClient,
    ApplicationClientScopeGrant,
    ApplicationClientSecret,
    ApplicationClientStatus,
    AuthAuditLog,
    PermissionScope,
)


@pytest.mark.db
async def test_issue_client_token_returns_token_for_valid_credentials(session):
    client, secret, plaintext_secret = await _create_client_with_secret_and_scopes(
        session,
        scopes=["bot:read", "bot:write"],
    )

    token = await issue_client_token(
        session,
        _settings(),
        client_id=client.client_id,
        client_secret=plaintext_secret,
        requested_scopes=["bot:write"],
        now=datetime.now(UTC),
    )
    principal = validate_access_token(token.access_token, _settings())
    audit_logs = await _token_audit_logs(session)

    assert token.scope == "bot:write"
    assert principal.principal_id == client.id
    assert principal.client_id == client.client_id
    assert principal.scopes == frozenset({"bot:write"})
    assert secret.last_used_at is not None
    assert [(log.event_type, log.success) for log in audit_logs] == [("token.issued", True)]


@pytest.mark.db
async def test_issue_client_token_uses_all_granted_active_scopes_when_none_requested(session):
    client, _secret, plaintext_secret = await _create_client_with_secret_and_scopes(
        session,
        scopes=["bot:write", "bot:read"],
    )

    token = await issue_client_token(
        session,
        _settings(),
        client_id=client.client_id,
        client_secret=plaintext_secret,
    )

    assert token.scope == "bot:read bot:write"


@pytest.mark.db
async def test_issue_client_token_accepts_space_separated_requested_scopes(session):
    client, _secret, plaintext_secret = await _create_client_with_secret_and_scopes(
        session,
        scopes=["bot:write", "bot:read"],
    )

    token = await issue_client_token(
        session,
        _settings(),
        client_id=client.client_id,
        client_secret=plaintext_secret,
        requested_scopes="bot:write bot:read",
    )

    assert token.scope == "bot:read bot:write"


@pytest.mark.db
async def test_issue_client_token_rejects_invalid_secret(session):
    client, _secret, _plaintext_secret = await _create_client_with_secret_and_scopes(session, scopes=["bot:read"])

    with pytest.raises(InvalidClientCredentialsError):
        await issue_client_token(
            session,
            _settings(),
            client_id=client.client_id,
            client_secret="wrong-secret",
        )

    audit_logs = await _token_audit_logs(session)
    assert [(log.event_type, log.success) for log in audit_logs] == [("token.denied", False)]


@pytest.mark.db
async def test_issue_client_token_rejects_disabled_client(session):
    client, _secret, plaintext_secret = await _create_client_with_secret_and_scopes(session, scopes=["bot:read"])
    client.status = ApplicationClientStatus.DISABLED
    await session.flush()

    with pytest.raises(InvalidClientCredentialsError):
        await issue_client_token(
            session,
            _settings(),
            client_id=client.client_id,
            client_secret=plaintext_secret,
        )


@pytest.mark.db
async def test_issue_client_token_rejects_revoked_secret(session):
    client, secret, plaintext_secret = await _create_client_with_secret_and_scopes(session, scopes=["bot:read"])
    secret.revoked_at = datetime.now(UTC)
    await session.flush()

    with pytest.raises(InvalidClientCredentialsError):
        await issue_client_token(
            session,
            _settings(),
            client_id=client.client_id,
            client_secret=plaintext_secret,
        )


@pytest.mark.db
async def test_issue_client_token_rejects_expired_secret(session):
    client, _secret, plaintext_secret = await _create_client_with_secret_and_scopes(
        session,
        scopes=["bot:read"],
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )

    with pytest.raises(InvalidClientCredentialsError):
        await issue_client_token(
            session,
            _settings(),
            client_id=client.client_id,
            client_secret=plaintext_secret,
        )


@pytest.mark.db
async def test_issue_client_token_rejects_ungranted_scope(session):
    client, _secret, plaintext_secret = await _create_client_with_secret_and_scopes(session, scopes=["bot:read"])

    with pytest.raises(InvalidClientScopeError):
        await issue_client_token(
            session,
            _settings(),
            client_id=client.client_id,
            client_secret=plaintext_secret,
            requested_scopes=["bot:write"],
        )


@pytest.mark.db
async def test_issue_client_token_ignores_inactive_grants(session):
    client, _secret, plaintext_secret = await _create_client_with_secret_and_scopes(
        session,
        scopes=["bot:read"],
        active=False,
    )

    with pytest.raises(InvalidClientScopeError):
        await issue_client_token(
            session,
            _settings(),
            client_id=client.client_id,
            client_secret=plaintext_secret,
        )


async def _create_client_with_secret_and_scopes(
    session,
    *,
    scopes: list[str],
    active: bool = True,
    expires_at: datetime | None = None,
) -> tuple[ApplicationClient, ApplicationClientSecret, str]:
    client = ApplicationClient(client_id="skillbot", name="SkillBot")
    session.add(client)
    await session.flush()

    created_secret = await create_client_secret(
        session,
        application_client_id=client.id,
        expires_at=expires_at,
    )

    for scope_key in scopes:
        permission_scope = PermissionScope(
            key=scope_key,
            description=f"{scope_key} scope",
            active=active,
        )
        session.add(permission_scope)
        session.add(ApplicationClientScopeGrant(application_client=client, permission_scope=permission_scope))

    await session.flush()
    return client, created_secret.secret, created_secret.plaintext


def _settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))


async def _token_audit_logs(session) -> list[AuthAuditLog]:
    return (
        (
            await session.execute(
                select(AuthAuditLog).where(AuthAuditLog.event_type.in_(["token.issued", "token.denied"]))
            )
        )
        .scalars()
        .all()
    )
