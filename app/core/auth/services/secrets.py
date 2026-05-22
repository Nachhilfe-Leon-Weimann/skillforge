import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClientSecret

from ..audit import AuditEventType, write_auth_audit_log
from ..results import CreatedClientSecret
from ..secrets import generate_client_secret, hash_client_secret
from .clients import get_application_client
from .errors import ApplicationClientSecretNotFoundError


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
    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=application_client_id,
        event_type=AuditEventType.CLIENT_SECRET_CREATED,
        success=True,
        detail=f"Created client secret {secret.id}.",
    )

    return CreatedClientSecret(
        plaintext=plaintext,
        secret=secret,
    )


async def create_application_client_secret(
    session: AsyncSession,
    *,
    client_id: str,
    label: str | None = None,
    expires_at: datetime | None = None,
) -> CreatedClientSecret:
    client = await get_application_client(session, client_id=client_id)
    return await create_client_secret(
        session,
        application_client_id=client.id,
        label=label,
        expires_at=expires_at,
    )


async def revoke_application_client_secret(
    session: AsyncSession,
    *,
    client_id: str,
    secret_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    client = await get_application_client(session, client_id=client_id)
    secret = await session.get(ApplicationClientSecret, secret_id)
    if secret is not None and secret.application_client_id != client.id:
        secret = None
    if secret is None:
        raise ApplicationClientSecretNotFoundError("Application client secret not found")

    secret.revoked_at = _normalize_datetime(now or datetime.now(UTC))
    await session.flush()
    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type=AuditEventType.CLIENT_SECRET_REVOKED,
        success=True,
        detail=f"Revoked client secret {secret.id}.",
    )


async def _client_has_usable_secret(session: AsyncSession, *, client_id: uuid.UUID, now: datetime) -> bool:
    secrets = (
        (
            await session.execute(
                select(ApplicationClientSecret).where(ApplicationClientSecret.application_client_id == client_id)
            )
        )
        .scalars()
        .all()
    )

    return any(_is_secret_usable(secret, now=now) for secret in secrets)


def _is_secret_usable(secret: ApplicationClientSecret, *, now: datetime) -> bool:
    if secret.revoked_at is not None:
        return False

    return secret.expires_at is None or _normalize_datetime(secret.expires_at) > now


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
