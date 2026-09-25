"""Setting a password on a person's behalf: redeeming a one-time token.

A client's route: `auth:users:login` in `application` mode, and a person's token is refused whatever it
carries.
"""

from typing import Annotated

from fastapi import APIRouter, status

from app.api.v1.common import DBSession, error_responses
from app.core.auth import ApplicationPrincipal, Scope
from app.core.auth.dependencies import require_application_scopes
from app.core.auth.services import action_tokens as action_tokens_service
from app.core.auth.services.errors import InvalidActionTokenError, WeakPasswordError

from .schemas import PasswordRedeemRequest

router = APIRouter(prefix="/password")

LoginClient = Annotated[ApplicationPrincipal, require_application_scopes(Scope.AUTH_USERS_LOGIN)]


@router.post(
    "/redeem",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(InvalidActionTokenError, WeakPasswordError),
)
async def redeem_password(request: PasswordRedeemRequest, session: DBSession, principal: LoginClient) -> None:
    """Set a password with an invitation or a password-reset token.

    An unknown, used, invalidated or expired token answers one and the same body. Redeeming a reset revokes
    every session of the account; the account's status does not change.
    """
    await action_tokens_service.redeem_action_token(
        session, plaintext=request.token, new_password=request.new_password, actor=principal
    )
