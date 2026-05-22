from pydantic import BaseModel

from app.core.auth.tokens import CreatedAccessToken


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
