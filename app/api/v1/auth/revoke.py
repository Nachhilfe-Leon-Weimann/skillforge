"""Ending a person's session on their behalf: the logout of a login client (RFC 7009 semantics)."""

from fastapi import APIRouter, status

from app.api.v1.common import DBSession
from app.core.auth.services import sessions as sessions_service

from .params import LoginClient
from .schemas import RefreshTokenRevokeRequest

router = APIRouter()


@router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_refresh_token(request: RefreshTokenRevokeRequest, session: DBSession, principal: LoginClient) -> None:
    """Log a person out: end the session a refresh token belongs to.

    Only a session this client opened is ended. The answer is the same whether a session was found or not, so
    the route tells nothing about a token. Access tokens already issued stay valid until they expire.
    """
    await sessions_service.revoke_session_by_refresh_token(
        session, refresh_token=request.refresh_token, client=principal
    )
