import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from .config import AuthSettings
from .principal import Principal

PRINCIPAL_TYPE_APPLICATION = "application"
TOKEN_TYPE_BEARER = "bearer"


class TokenValidationError(ValueError):
    """Raised when an access token cannot be trusted or converted into a principal."""


@dataclass(frozen=True)
class CreatedAccessToken:
    access_token: str
    token_type: str
    expires_at: datetime
    expires_in: int
    scope: str


def create_application_access_token(
    settings: AuthSettings,
    *,
    principal_id: uuid.UUID,
    client_id: str,
    scopes: Iterable[str],
    now: datetime | None = None,
) -> CreatedAccessToken:
    issued_at = _normalize_datetime(now or datetime.now(UTC))
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    scope = _format_scope(scopes)

    claims = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": f"app:{client_id}",
        "principal_type": PRINCIPAL_TYPE_APPLICATION,
        "principal_id": str(principal_id),
        "azp": client_id,
        "scope": scope,
        "iat": issued_at,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
    }

    access_token = jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )

    return CreatedAccessToken(
        access_token=access_token,
        token_type=TOKEN_TYPE_BEARER,
        expires_at=expires_at,
        expires_in=int((expires_at - issued_at).total_seconds()),
        scope=scope,
    )


def validate_access_token(token: str, settings: AuthSettings) -> Principal:
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            issuer=settings.issuer,
            audience=settings.audience,
            options={
                "require": [
                    "iss",
                    "aud",
                    "sub",
                    "principal_type",
                    "principal_id",
                    "azp",
                    "scope",
                    "iat",
                    "exp",
                    "jti",
                ],
            },
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError("Invalid access token") from exc

    return _claims_to_principal(claims)


def _claims_to_principal(claims: dict[str, object]) -> Principal:
    try:
        principal_type = str(claims.get("principal_type"))
    except ValueError as exc:
        raise TokenValidationError("Invalid principal type") from exc
    if principal_type != PRINCIPAL_TYPE_APPLICATION:
        raise TokenValidationError("Unsupported principal type")

    client_id = _require_str_claim(claims, "azp")
    subject = _require_str_claim(claims, "sub")
    if subject != f"app:{client_id}":
        raise TokenValidationError("Invalid subject")

    scope = _require_str_claim(claims, "scope")
    scopes = frozenset(scope.split())
    if not scopes:
        raise TokenValidationError("Missing token scope")

    try:
        principal_id = uuid.UUID(_require_str_claim(claims, "principal_id"))
    except ValueError as exc:
        raise TokenValidationError("Invalid principal id") from exc

    return Principal(
        principal_type=principal_type,
        principal_id=principal_id,
        subject=subject,
        scopes=scopes,
        client_id=client_id,
    )


def _require_str_claim(claims: dict[str, object], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise TokenValidationError(f"Missing or invalid {name} claim")

    return value


def _format_scope(scopes: Iterable[str]) -> str:
    normalized_scopes = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    if not normalized_scopes:
        raise ValueError("scopes must not be empty")

    return " ".join(normalized_scopes)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
