import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db.models import (
    ApplicationClient,
    ApplicationClientScopeGrant,
    ApplicationClientSecret,
    ApplicationClientStatus,
    AuthAuditLog,
)

from .config import AuthSettings
from .secrets import generate_client_secret, hash_client_secret, verify_client_secret
from .tokens import CreatedAccessToken, create_application_access_token


@dataclass(frozen=True)
class CreatedClientSecret:
    plaintext: str
    secret: ApplicationClientSecret


class ClientCredentialsError(ValueError):
    """Raised when client credentials cannot be exchanged for an access token."""


class InvalidClientCredentialsError(ClientCredentialsError):
    """Raised for unknown clients, disabled clients, or invalid client secrets."""


class InvalidClientScopeError(ClientCredentialsError):
    """Raised when requested scopes are unknown, inactive, or not granted to the client."""


async def create_client_secret(
    session: AsyncSession,
    *,
    application_client_id: uuid.UUID,
    label: str | None = None,
    expires_at: datetime | None = None,
) -> CreatedClientSecret:
    plaintext = generate_client_secret()
    secret = ApplicationClientSecret(
        application_client_id=application_client_id,
        secret_hash=hash_client_secret(plaintext),
        label=label,
        expires_at=expires_at,
    )

    session.add(secret)
    await session.flush()
    await _write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=application_client_id,
        event_type="client_secret.created",
        success=True,
        detail=f"Created client secret {secret.id}.",
    )

    return CreatedClientSecret(
        plaintext=plaintext,
        secret=secret,
    )


async def issue_client_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    client_id: str,
    client_secret: str,
    requested_scopes: Iterable[str] | str | None = None,
    now: datetime | None = None,
) -> CreatedAccessToken:
    issued_at = _normalize_datetime(now or datetime.now(UTC))
    client = await _get_application_client(session, client_id)

    try:
        if client is None or client.status != ApplicationClientStatus.ACTIVE:
            raise InvalidClientCredentialsError("Invalid client credentials")

        matching_secret = _find_matching_secret(
            client.secrets,
            client_secret=client_secret,
            now=issued_at,
        )
        if matching_secret is None:
            raise InvalidClientCredentialsError("Invalid client credentials")

        granted_scopes = _granted_active_scope_keys(client.scope_grants)
        token_scopes = _resolve_token_scopes(
            requested_scopes=requested_scopes,
            granted_scopes=granted_scopes,
        )

        matching_secret.last_used_at = issued_at
        token = create_application_access_token(
            settings,
            principal_id=client.id,
            client_id=client.client_id,
            scopes=token_scopes,
            now=issued_at,
        )
    except ClientCredentialsError as exc:
        await _write_auth_audit_log(
            session,
            principal_type="application",
            principal_id=client.id if client is not None else client_id,
            event_type="token.denied",
            success=False,
            detail=str(exc),
        )
        raise

    await _write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type="token.issued",
        success=True,
        detail=f"Issued client credentials token for {client.client_id}.",
    )

    await session.flush()
    return token


async def _get_application_client(session: AsyncSession, client_id: str) -> ApplicationClient | None:
    result = await session.execute(
        select(ApplicationClient)
        .where(ApplicationClient.client_id == client_id)
        .options(
            selectinload(ApplicationClient.secrets),
            selectinload(ApplicationClient.scope_grants).selectinload(ApplicationClientScopeGrant.permission_scope),
        )
    )

    return result.scalar_one_or_none()


def _find_matching_secret(
    secrets: list[ApplicationClientSecret],
    *,
    client_secret: str,
    now: datetime,
) -> ApplicationClientSecret | None:
    for secret in secrets:
        if not _is_secret_usable(secret, now=now):
            continue

        if verify_client_secret(client_secret, secret.secret_hash):
            return secret

    return None


def _is_secret_usable(secret: ApplicationClientSecret, *, now: datetime) -> bool:
    if secret.revoked_at is not None:
        return False

    return secret.expires_at is None or _normalize_datetime(secret.expires_at) > now


def _granted_active_scope_keys(scope_grants: list[ApplicationClientScopeGrant]) -> frozenset[str]:
    return frozenset(
        grant.scope_key
        for grant in scope_grants
        if grant.permission_scope is not None and grant.permission_scope.active
    )


def _resolve_token_scopes(
    *,
    requested_scopes: Iterable[str] | str | None,
    granted_scopes: frozenset[str],
) -> frozenset[str]:
    normalized_requested_scopes = _normalize_scope_set(requested_scopes)
    if not normalized_requested_scopes:
        if not granted_scopes:
            raise InvalidClientScopeError("Client has no active scope grants")
        return granted_scopes

    missing_scopes = normalized_requested_scopes - granted_scopes
    if missing_scopes:
        raise InvalidClientScopeError("Requested scopes are not granted")

    return normalized_requested_scopes


def _normalize_scope_set(scopes: Iterable[str] | str | None) -> frozenset[str]:
    if scopes is None:
        return frozenset()

    if isinstance(scopes, str):
        return frozenset(scopes.split())

    return frozenset(str(scope).strip() for scope in scopes if str(scope).strip())


async def _write_auth_audit_log(
    session: AsyncSession,
    *,
    principal_type: str | None,
    principal_id: str | uuid.UUID | None,
    event_type: str,
    success: bool,
    detail: str | None = None,
) -> None:
    session.add(
        AuthAuditLog(
            principal_type=principal_type,
            principal_id=str(principal_id) if principal_id is not None else None,
            event_type=event_type,
            success=success,
            detail=detail,
        )
    )
    await session.flush()


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
