from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.common import ApiModel
from app.core.auth.principal import Principal
from app.core.auth.results import CreatedClientSecret
from app.core.auth.tokens import CreatedAccessToken
from app.core.db.models import ApplicationClient, ApplicationClientStatus


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


class ApplicationClientScopeGrantRequest(BaseModel):
    scopes: list[str] = Field(min_length=1)


class ApplicationClientSecretResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApplicationClientResponse(BaseModel):
    id: UUID
    client_id: str
    name: str
    description: str | None
    status: ApplicationClientStatus
    scopes: list[str]
    secrets: list[ApplicationClientSecretResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, client: ApplicationClient) -> ApplicationClientResponse:
        return cls(
            id=client.id,
            client_id=client.client_id,
            name=client.name,
            description=client.description,
            status=client.status,
            scopes=sorted(grant.scope_key for grant in client.scope_grants),
            secrets=[
                ApplicationClientSecretResponse.model_validate(secret)
                for secret in sorted(client.secrets, key=lambda item: item.created_at)
            ],
            created_at=client.created_at,
            updated_at=client.updated_at,
        )


class CreatedClientSecretResponse(BaseModel):
    client_secret: str
    secret: ApplicationClientSecretResponse

    @classmethod
    def from_created_secret(cls, created_secret: CreatedClientSecret) -> CreatedClientSecretResponse:
        return cls(
            client_secret=created_secret.plaintext,
            secret=ApplicationClientSecretResponse.model_validate(created_secret.secret),
        )
