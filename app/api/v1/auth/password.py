"""Setting a password on a user's behalf: the route that redeems a one-time token.

It acts on behalf of a user, so it is a client's to call: guarded by ``require_application`` with
`auth:users:login` - a user token must never redeem a token, no matter what it carries.
"""

from typing import Annotated

from fastapi import APIRouter, Security, status

from app.api.v1.common import DBSession, error_responses
from app.core.auth import ApplicationPrincipal, Scope, require_application
from app.core.auth.services import action_tokens as action_tokens_service
from app.core.auth.services.errors import InvalidActionTokenError, WeakPasswordError

from .schemas import PasswordRedeemRequest

router = APIRouter(prefix="/password")

# One declaration: the scope reaches the nested ``get_current_principal`` - and with it the
# operation's `security` and its 403 - and ``require_application`` refuses a user token.
LoginClient = Annotated[ApplicationPrincipal, Security(require_application, scopes=[Scope.AUTH_USERS_LOGIN])]


@router.post(
    "/redeem",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(InvalidActionTokenError, WeakPasswordError),
)
async def redeem_password(request: PasswordRedeemRequest, session: DBSession, principal: LoginClient) -> None:
    """Set a password with an invitation or a password-reset token.

    An unknown, used, expired or replaced token answers the same body, so a caller cannot tell
    which of the four it was. Redeeming a reset revokes every session of the account.
    """
    await action_tokens_service.redeem_action_token(
        session,
        plaintext=request.token,
        new_password=request.new_password,
        actor=principal.actor,
    )
