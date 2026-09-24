from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.common import ApiModel
from app.core.auth.principal import Principal
from app.core.auth.results import CreatedClientSecret
from app.core.auth.tokens import CreatedAccessToken
from app.core.db.models import ApplicationClient, ApplicationClientStatus, GrantMode


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    scope: str

    @classmethod
    def from_created_token(cls, token: CreatedAccessToken) -> AccessTokenResponse:
        return cls(
            access_token=token.access_token,
            token_type=token.token_type,
            expires_in=token.expires_in,
            scope=token.scope,
        )


class MeResponse(ApiModel):
    """What the calling token says about its bearer."""

    principal_type: str
    """Kind of principal the token was issued to; `application` for an application client."""
    client_id: str | None
    """Client ID of the application client; `null` for a principal that is not a client."""
    scopes: list[str]
    """Scopes the token grants, sorted."""

    @classmethod
    def from_principal(cls, principal: Principal) -> MeResponse:
        return cls(
            principal_type=principal.principal_type,
            client_id=principal.client_id,
            scopes=sorted(principal.scopes),
        )


class ApplicationClientCreateRequest(BaseModel):
    client_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None


class ApplicationClientUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: ApplicationClientStatus | None = None


class ApplicationClientSecretCreateRequest(BaseModel):
    label: str | None = None
    expires_at: datetime | None = None


class ApplicationClientScopeGrantRequest(ApiModel):
    """Scopes to grant an application client in one mode (ADR 0008)."""

    scopes: list[str] = Field(min_length=1)
    """Scopes to grant, e.g. `crm:read`. A scope the client already holds in `mode` stays as it is; if one scope is
    refused, none is granted."""
    mode: GrantMode
    """`application`: what the client may do for itself (`client_credentials`). `delegated`: the most it may do for a
    person it acts for; a client-only scope such as `auth:users:login` is refused in this mode."""


class ApplicationClientSecretResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApplicationClientResponse(ApiModel):
    """An application client with its scope grants and secrets."""

    id: UUID
    """ID of the client; its application tokens carry it as `principal_id`."""
    client_id: str
    """Identifier the client authenticates with at `POST /auth/token`."""
    name: str
    """Display name of the client."""
    description: str | None
    """Free-text description; `null` when there is none."""
    status: ApplicationClientStatus
    """An `active` client obtains tokens, a `disabled` one does not."""
    application_scopes: list[str]
    """Scopes granted in `application` mode - what the client may do for itself - sorted."""
    delegated_scopes: list[str]
    """Scopes granted in `delegated` mode - the most the client may do for a person - sorted."""
    secrets: list[ApplicationClientSecretResponse]
    """Secrets of the client, oldest first, without their values."""
    created_at: datetime
    """When the client was created."""
    updated_at: datetime
    """When the client was last changed."""

    @classmethod
    def from_model(cls, client: ApplicationClient) -> ApplicationClientResponse:
        return cls(
            id=client.id,
            client_id=client.client_id,
            name=client.name,
            description=client.description,
            status=client.status,
            application_scopes=_granted_scopes(client, GrantMode.APPLICATION),
            delegated_scopes=_granted_scopes(client, GrantMode.DELEGATED),
            secrets=[
                ApplicationClientSecretResponse.model_validate(secret)
                for secret in sorted(client.secrets, key=lambda item: item.created_at)
            ],
            created_at=client.created_at,
            updated_at=client.updated_at,
        )


def _granted_scopes(client: ApplicationClient, mode: GrantMode) -> list[str]:
    return sorted(grant.scope_key for grant in client.scope_grants if grant.mode == mode)


class CreatedClientSecretResponse(BaseModel):
    client_secret: str
    secret: ApplicationClientSecretResponse

    @classmethod
    def from_created_secret(cls, created_secret: CreatedClientSecret) -> CreatedClientSecretResponse:
        return cls(
            client_secret=created_secret.plaintext,
            secret=ApplicationClientSecretResponse.model_validate(created_secret.secret),
        )
