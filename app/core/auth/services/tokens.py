from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import ApplicationClientSecret, ApplicationClientStatus

from ..audit import AuditEventType, write_auth_audit_log
from ..config import AuthSettings
from ..secrets import verify_client_secret
from ..tokens import CreatedAccessToken, create_application_access_token
from .clients import find_application_client
from .errors import ClientCredentialsError, InvalidClientCredentialsError
from .scopes import granted_active_scope_keys, resolve_token_scopes
from .secrets import is_secret_usable, normalize_datetime


async def issue_client_token(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    client_id: str,
    client_secret: str,
    requested_scopes: Iterable[str] | str | None = None,
    now: datetime | None = None,
) -> CreatedAccessToken:
    issued_at = normalize_datetime(now or datetime.now(UTC))
    client = await find_application_client(session, client_id)

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

        granted_scopes = granted_active_scope_keys(client.scope_grants)
        token_scopes = resolve_token_scopes(
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
        await write_auth_audit_log(
            session,
            principal_type="application",
            principal_id=client.id if client is not None else client_id,
            event_type=AuditEventType.TOKEN_DENIED,
            success=False,
            detail=str(exc),
        )
        raise

    await write_auth_audit_log(
        session,
        principal_type="application",
        principal_id=client.id,
        event_type=AuditEventType.TOKEN_ISSUED,
        success=True,
        detail=f"Issued client credentials token for {client.client_id}.",
    )

    await session.flush()
    return token


def _find_matching_secret(
    secrets: list[ApplicationClientSecret],
    *,
    client_secret: str,
    now: datetime,
) -> ApplicationClientSecret | None:
    for secret in secrets:
        if not is_secret_usable(secret, now=now):
            continue

        if verify_client_secret(client_secret, secret.secret_hash):
            return secret

    return None
