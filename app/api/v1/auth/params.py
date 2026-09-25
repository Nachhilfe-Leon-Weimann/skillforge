"""Parameter aliases the auth routes share."""

from typing import Annotated

from app.core.auth import ApplicationPrincipal, Scope
from app.core.auth.dependencies import require_application_scopes

LoginClient = Annotated[ApplicationPrincipal, require_application_scopes(Scope.AUTH_USERS_LOGIN)]
"""A client acting on a person's behalf: `auth:users:login` in `application` mode; a person's token is refused
whatever it carries."""
