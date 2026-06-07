from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_response
from app.core.db.dependencies import get_db_session
from app.services.bot import PrincipalNotFoundError, check_authorization

from .dependencies import BotRead
from .schemas import AuthorizationCheckRequest, AuthorizationCheckResponse

router = APIRouter(prefix="/authz")

ACTOR_NOT_FOUND = "Actor principal not found"


@router.post(
    "/check",
    response_model=AuthorizationCheckResponse,
    responses={404: error_response(ACTOR_NOT_FOUND)},
)
async def check_authorization_endpoint(
    request: AuthorizationCheckRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: BotRead,
) -> AuthorizationCheckResponse:
    """Check whether an actor may perform an action on a target party (direct or delegated).

    Combines the grant engine (*whether* the actor may run `action_key`) with the delegation set
    derived from `PartyRelation` (*on whose behalf* -- own party plus `PARENT_OF` / `PAYS_FOR`
    targets). An inactive actor is denied; an unknown actor returns 404.
    """
    try:
        allowed = await check_authorization(
            session,
            actor_discord_id=request.actor_discord_id,
            action_key=request.action_key,
            target_party_id=request.target_party_id,
        )
    except PrincipalNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ACTOR_NOT_FOUND) from exc

    return AuthorizationCheckResponse(allowed=allowed)
