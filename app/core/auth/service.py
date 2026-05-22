import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClientSecret

from .secrets import generate_client_secret, hash_client_secret


@dataclass(frozen=True)
class CreatedClientSecret:
    plaintext: str
    secret: ApplicationClientSecret


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

    return CreatedClientSecret(
        plaintext=plaintext,
        secret=secret,
    )
