"""User access tokens: what `create_user_access_token` writes and what validation accepts back.

The application token is pinned here too: its claims are the contract SkillBot already lives on, so
this file asserts them against a fixture that a new claim would break.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from pydantic import SecretStr

from app.core.auth import (
    AuthSettings,
    CreatedAccessToken,
    TokenValidationError,
    create_application_access_token,
    create_user_access_token,
    validate_access_token,
)
from app.core.auth.tokens import PRINCIPAL_TYPE_USER

ISSUED_AT = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
EXPIRES_AT = ISSUED_AT + timedelta(minutes=15)
USER_ID = UUID("11111111-1111-1111-1111-111111111111")
PARTY_ID = UUID("22222222-2222-2222-2222-222222222222")
SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
APPLICATION_ID = UUID("44444444-4444-4444-4444-444444444444")


def test_application_access_token_claims_match_the_fixture():
    """The shape SkillBot's tokens have today. A new claim on an application token breaks this."""
    settings = _settings()

    created = create_application_access_token(
        settings,
        principal_id=APPLICATION_ID,
        client_id="skillbot",
        scopes=["bot:write", "bot:read"],
        now=ISSUED_AT,
    )

    claims = _decode(created.access_token, settings)
    assert UUID(str(claims.pop("jti")))
    assert claims == {
        "iss": "skillforge",
        "aud": "skillforge-api",
        "sub": "app:skillbot",
        "principal_type": "application",
        "principal_id": str(APPLICATION_ID),
        "azp": "skillbot",
        "scope": "bot:read bot:write",
        "iat": int(ISSUED_AT.timestamp()),
        "exp": int(EXPIRES_AT.timestamp()),
    }


def test_user_access_token_claims_match_the_fixture():
    settings = _settings()

    created = _user_token(settings, scopes=["crm:read:own", "account:self"], roles=["tutor", "admin"], now=ISSUED_AT)

    claims = _decode(created.access_token, settings)
    assert UUID(str(claims.pop("jti")))
    assert claims == {
        "iss": "skillforge",
        "aud": "skillforge-api",
        "sub": f"user:{USER_ID}",
        "principal_type": "user",
        "principal_id": str(USER_ID),
        "azp": "portal",
        "scope": "account:self crm:read:own",
        "party_id": str(PARTY_ID),
        "sid": str(SESSION_ID),
        "roles": ["admin", "tutor"],
        "iat": int(ISSUED_AT.timestamp()),
        "exp": int(EXPIRES_AT.timestamp()),
    }


def test_user_access_token_carries_the_canonical_scope():
    """A token never holds both a scope and its `:own` variant (ADR 0008, decision H)."""
    settings = _settings()

    created = _user_token(settings, scopes=["crm:read", "crm:read:own", "account:self"])

    assert created.scope == "account:self crm:read"


def test_create_and_validate_user_access_token():
    settings = _settings()

    created = _user_token(settings, scopes=["account:self", "crm:read:own"], roles=["admin"])
    principal = validate_access_token(created.access_token, settings)

    assert principal.principal_type == PRINCIPAL_TYPE_USER
    assert principal.principal_id == USER_ID
    assert principal.subject == f"user:{USER_ID}"
    assert principal.client_id == "portal"
    assert principal.scopes == frozenset({"account:self", "crm:read:own"})
    assert principal.party_id == PARTY_ID
    assert principal.session_id == SESSION_ID
    assert principal.roles == frozenset({"admin"})


def test_an_application_principal_carries_no_user_claims():
    settings = _settings()
    created = create_application_access_token(
        settings,
        principal_id=APPLICATION_ID,
        client_id="skillbot",
        scopes=["bot:read"],
    )

    principal = validate_access_token(created.access_token, settings)

    assert principal.party_id is None
    assert principal.session_id is None
    assert principal.roles == frozenset()


@pytest.mark.parametrize("claim", ["party_id", "sid"])
def test_validate_access_token_rejects_a_user_token_without_its_reach_claims(claim: str):
    settings = _settings()
    token = _encode_user_claims(settings, without=claim)

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_a_user_token_whose_subject_is_not_its_principal():
    settings = _settings()
    token = _encode_user_claims(settings, subject=f"user:{uuid4()}")

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_a_user_token_with_an_application_subject():
    settings = _settings()
    token = _encode_user_claims(settings, subject="app:portal")

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_a_user_token_with_an_unusable_party_id():
    settings = _settings()
    token = _encode_user_claims(settings, party_id="not-a-uuid")

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_a_user_token_with_malformed_roles():
    settings = _settings()
    token = _encode_user_claims(settings, roles="admin")

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_an_unknown_principal_type():
    settings = _settings()
    token = _encode_user_claims(settings, principal_type="service")

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_create_user_access_token_rejects_empty_scopes():
    with pytest.raises(ValueError, match="scopes must not be empty"):
        _user_token(_settings(), scopes=[])


def _user_token(
    settings: AuthSettings,
    *,
    scopes: list[str],
    roles: list[str] | None = None,
    now: datetime | None = None,
) -> CreatedAccessToken:
    return create_user_access_token(
        settings,
        principal_id=USER_ID,
        client_id="portal",
        party_id=PARTY_ID,
        session_id=SESSION_ID,
        scopes=scopes,
        roles=roles or [],
        now=now or datetime.now(UTC),
    )


def _decode(token: str, settings: AuthSettings) -> dict[str, object]:
    """Decode without the expiry check: the fixtures pin a fixed issuing time."""
    return jwt.decode(
        token,
        settings.secret_key.get_secret_value(),
        algorithms=[settings.algorithm],
        issuer=settings.issuer,
        audience=settings.audience,
        options={"verify_exp": False},
    )


def _encode_user_claims(
    settings: AuthSettings,
    *,
    principal_type: str = "user",
    subject: str | None = None,
    party_id: str | None = None,
    roles: object = None,
    without: str | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": subject or f"user:{USER_ID}",
        "principal_type": principal_type,
        "principal_id": str(USER_ID),
        "azp": "portal",
        "scope": "account:self",
        "party_id": party_id or str(PARTY_ID),
        "sid": str(SESSION_ID),
        "roles": roles if roles is not None else [],
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid4()),
    }
    claims.pop(without, None)

    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def _settings() -> AuthSettings:
    return AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
