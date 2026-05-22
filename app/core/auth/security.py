from fastapi import Request
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param

from .scopes import Scope


class OAuth2ClientCredentialsBearer(OAuth2):
    async def __call__(self, request: Request) -> str | None:
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            if self.auto_error:
                raise self.make_not_authenticated_error()
            return None

        return param


oauth2_scheme = OAuth2ClientCredentialsBearer(
    flows=OAuthFlows(
        clientCredentials=OAuthFlowClientCredentials(
            tokenUrl="/api/v1/auth/token",
            scopes={scope.value: scope.name for scope in Scope},
        )
    ),
    auto_error=False,
)
