import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from .config import AuthSettings
from .principal import Principal
from .scopes import canonical

PRINCIPAL_TYPE_APPLICATION = "application"
PRINCIPAL_TYPE_USER = "user"
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
    scopes: Iterable[str] | str,
    now: datetime | None = None,
) -> CreatedAccessToken:
    issued_at = _normalize_datetime(now or datetime.now(UTC))
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    scope = _format_scope(scopes)

    claims = _base_claims(
        settings,
        subject=f"app:{client_id}",
        principal_type=PRINCIPAL_TYPE_APPLICATION,
        principal_id=principal_id,
        client_id=client_id,
        scope=scope,
        issued_at=issued_at,
        expires_at=expires_at,
    )

    return _encode(settings, claims, issued_at=issued_at, expires_at=expires_at, scope=scope)


def create_user_access_token(
    settings: AuthSettings,
    *,
    principal_id: uuid.UUID,
    client_id: str,
    party_id: uuid.UUID,
    session_id: uuid.UUID,
    scopes: Iterable[str] | str,
    roles: Iterable[str] | str = (),
    now: datetime | None = None,
) -> CreatedAccessToken:
    """Issue an access token for a user account, to ``client_id`` on that user's behalf.

    ``principal_id`` is the ``auth.user_account`` row, ``session_id`` the ``auth.user_session`` the
    login opened. ``roles`` are informational - Forge authorizes by scope only (ADR 0008) - and the
    scope claim is canonical, so a token never carries both a scope and its ``:own`` variant.
    """
    issued_at = _normalize_datetime(now or datetime.now(UTC))
    expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
    scope = _format_scope(canonical(scopes))

    claims = _base_claims(
        settings,
        subject=f"user:{principal_id}",
        principal_type=PRINCIPAL_TYPE_USER,
        principal_id=principal_id,
        client_id=client_id,
        scope=scope,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    claims["party_id"] = str(party_id)
    claims["sid"] = str(session_id)
    claims["roles"] = sorted(_role_values(roles))

    return _encode(settings, claims, issued_at=issued_at, expires_at=expires_at, scope=scope)


def _base_claims(
    settings: AuthSettings,
    *,
    subject: str,
    principal_type: str,
    principal_id: uuid.UUID,
    client_id: str,
    scope: str,
    issued_at: datetime,
    expires_at: datetime,
) -> dict[str, object]:
    """The claims every access token carries, whatever it was issued for."""
    return {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": subject,
        "principal_type": principal_type,
        "principal_id": str(principal_id),
        "azp": client_id,
        "scope": scope,
        "iat": issued_at,
        "exp": expires_at,
        "jti": str(uuid.uuid4()),
    }


def _encode(
    settings: AuthSettings,
    claims: dict[str, object],
    *,
    issued_at: datetime,
    expires_at: datetime,
    scope: str,
) -> CreatedAccessToken:
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
    """Turn decoded claims into a principal, validating what the token's type has to carry."""
    principal_type = claims.get("principal_type")
    if principal_type == PRINCIPAL_TYPE_APPLICATION:
        return _application_principal(claims)
    if principal_type == PRINCIPAL_TYPE_USER:
        return _user_principal(claims)

    raise TokenValidationError("Unsupported principal type")


def _application_principal(claims: dict[str, object]) -> Principal:
    client_id = _require_str_claim(claims, "azp")
    subject = _require_str_claim(claims, "sub")
    if subject != f"app:{client_id}":
        raise TokenValidationError("Invalid subject")

    return Principal(
        principal_type=PRINCIPAL_TYPE_APPLICATION,
        principal_id=_require_uuid_claim(claims, "principal_id"),
        subject=subject,
        scopes=_require_scopes(claims),
        client_id=client_id,
    )


def _user_principal(claims: dict[str, object]) -> Principal:
    client_id = _require_str_claim(claims, "azp")
    # Compared as written, not as parsed: ``uuid.UUID`` also accepts the hyphen-less, uppercase,
    # brace and urn forms, so a parsed comparison would let the two claims disagree textually.
    principal_id = _require_str_claim(claims, "principal_id")
    subject = _require_str_claim(claims, "sub")
    if subject != f"user:{principal_id}":
        raise TokenValidationError("Invalid subject")

    return Principal(
        principal_type=PRINCIPAL_TYPE_USER,
        principal_id=_require_uuid_claim(claims, "principal_id"),
        subject=subject,
        scopes=_require_scopes(claims),
        client_id=client_id,
        party_id=_require_uuid_claim(claims, "party_id"),
        session_id=_require_uuid_claim(claims, "sid"),
        roles=_require_roles(claims),
    )


def _require_str_claim(claims: dict[str, object], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise TokenValidationError(f"Missing or invalid {name} claim")

    return value


def _require_uuid_claim(claims: dict[str, object], name: str) -> uuid.UUID:
    try:
        return uuid.UUID(_require_str_claim(claims, name))
    except ValueError as exc:
        raise TokenValidationError(f"Invalid {name} claim") from exc


def _require_scopes(claims: dict[str, object]) -> frozenset[str]:
    scopes = frozenset(_require_str_claim(claims, "scope").split())
    if not scopes:
        raise TokenValidationError("Missing token scope")

    return scopes


def _require_roles(claims: dict[str, object]) -> frozenset[str]:
    roles = claims.get("roles")
    if not isinstance(roles, list) or not all(isinstance(role, str) and role for role in roles):
        raise TokenValidationError("Missing or invalid roles claim")

    return frozenset(roles)


def _role_values(roles: Iterable[str] | str) -> frozenset[str]:
    """Normalize ``roles`` the way ``_scope_values`` normalizes scopes: a bare ``str`` is a
    whitespace-separated list of roles, not an iterable of characters."""
    if isinstance(roles, str):
        return frozenset(roles.split())

    return frozenset(value for value in (str(role).strip() for role in roles) if value)


def _format_scope(scopes: Iterable[str] | str) -> str:
    if isinstance(scopes, str):
        normalized_scopes = sorted(set(scopes.split()))
    else:
        normalized_scopes = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    if not normalized_scopes:
        raise ValueError("scopes must not be empty")

    return " ".join(normalized_scopes)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)

    return value.astimezone(UTC)
