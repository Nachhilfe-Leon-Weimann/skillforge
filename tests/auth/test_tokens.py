from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from app.core.auth import AuthSettings, TokenValidationError, create_application_access_token, validate_access_token
from app.core.auth.tokens import PRINCIPAL_TYPE_APPLICATION


def test_create_and_validate_application_access_token():
    settings = _settings()
    principal_id = uuid4()

    created = create_application_access_token(
        settings,
        principal_id=principal_id,
        client_id="some-client",
        scopes=["data:write", "data:read"],
        now=datetime.now(UTC),
    )
    principal = validate_access_token(created.access_token, settings)

    assert created.token_type == "bearer"
    assert created.expires_in == 900
    assert created.scope == "data:read data:write"
    assert principal.principal_type == PRINCIPAL_TYPE_APPLICATION
    assert principal.principal_id == principal_id
    assert principal.subject == "app:some-client"
    assert principal.client_id == "some-client"
    assert principal.scopes == frozenset({"data:read", "data:write"})


def test_validate_access_token_rejects_expired_token():
    settings = _settings()
    created = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="some-client",
        scopes=["data:read"],
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(TokenValidationError):
        validate_access_token(created.access_token, settings)


def test_validate_access_token_rejects_wrong_issuer():
    settings = _settings()
    created = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="some-client",
        scopes=["data:read"],
    )

    with pytest.raises(TokenValidationError):
        validate_access_token(created.access_token, _settings(issuer="other-issuer"))


def test_validate_access_token_rejects_wrong_audience():
    settings = _settings()
    created = create_application_access_token(
        settings,
        principal_id=uuid4(),
        client_id="some-client",
        scopes=["data:read"],
    )

    with pytest.raises(TokenValidationError):
        validate_access_token(created.access_token, _settings(audience="other-api"))


def test_validate_access_token_rejects_unsupported_principal_type():
    settings = _settings()
    token = _encode_claims(
        settings,
        principal_type="user",
        subject="user:123",
        client_id="some-client",
        scopes="data:read",
    )

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_missing_scope_claim():
    settings = _settings()
    token = _encode_claims(settings, scopes=None)

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_validate_access_token_rejects_subject_client_mismatch():
    settings = _settings()
    token = _encode_claims(settings, subject="app:other-client", client_id="some-client")

    with pytest.raises(TokenValidationError):
        validate_access_token(token, settings)


def test_create_application_access_token_rejects_empty_scopes():
    with pytest.raises(ValueError, match="scopes must not be empty"):
        create_application_access_token(
            _settings(),
            principal_id=uuid4(),
            client_id="some-client",
            scopes=[],
        )


def _settings(
    *,
    issuer: str = "skillforge",
    audience: str = "skillforge-api",
    secret_key: str = "test-signing-secret-with-at-least-32-bytes",
) -> AuthSettings:
    return AuthSettings(
        issuer=issuer,
        audience=audience,
        secret_key=SecretStr(secret_key),
    )


def _encode_claims(
    settings: AuthSettings,
    *,
    principal_type: str = PRINCIPAL_TYPE_APPLICATION,
    principal_id: str | None = None,
    subject: str = "app:some-client",
    client_id: str = "some-client",
    scopes: str | None = "data:read",
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": settings.issuer,
        "aud": settings.audience,
        "sub": subject,
        "principal_type": principal_type,
        "principal_id": principal_id or str(uuid4()),
        "azp": client_id,
        "iat": now,
        "exp": now + timedelta(minutes=15),
        "jti": str(uuid4()),
    }
    if scopes is not None:
        claims["scope"] = scopes

    return jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
