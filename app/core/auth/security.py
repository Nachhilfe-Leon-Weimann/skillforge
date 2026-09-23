from fastapi import Request
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlowPassword, OAuthFlows
from fastapi.security import OAuth2
from fastapi.security.utils import get_authorization_scheme_param

from .scopes import Scope

TOKEN_URL = "/api/v1/auth/token"


class OAuth2Bearer(OAuth2):
    async def __call__(self, request: Request) -> str | None:
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "bearer":
            if self.auto_error:
                raise self.make_not_authenticated_error()
            return None

        return param


_scopes = {scope.value: scope.description for scope in Scope}

oauth2_scheme = OAuth2Bearer(
    flows=OAuthFlows(
        clientCredentials=OAuthFlowClientCredentials(tokenUrl=TOKEN_URL, scopes=_scopes),
        # Swagger UI's "Authorize" dialog logs a user in through the same endpoint.
        password=OAuthFlowPassword(tokenUrl=TOKEN_URL, scopes=_scopes),
    ),
    # Named explicitly: FastAPI would otherwise derive the key in ``components.securitySchemes``
    # from the class name, and a later rename of the class would silently change the contract.
    scheme_name="OAuth2",
    auto_error=False,
)
