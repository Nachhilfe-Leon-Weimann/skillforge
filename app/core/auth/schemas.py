from dataclasses import dataclass

from pydantic import BaseModel

from app.core.db.models import ApplicationClient, ApplicationClientSecret

from .tokens import CreatedAccessToken


@dataclass(frozen=True)
class CreatedClientSecret:
    plaintext: str
    secret: ApplicationClientSecret


@dataclass(frozen=True)
class BootstrappedApplicationClient:
    client: ApplicationClient
    created_client: bool
    created_secret: CreatedClientSecret | None
    granted_scopes: frozenset[str]


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
