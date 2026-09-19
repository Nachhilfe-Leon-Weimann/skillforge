from typing import Annotated

from fastapi import APIRouter

from app.core.auth import Principal
from app.core.auth.dependencies import require_scopes

from .schemas import MeResponse

router = APIRouter()

# No scope: any valid token may ask what it carries.
AnyPrincipal = Annotated[Principal, require_scopes()]


@router.get("/me")
async def get_me(principal: AnyPrincipal) -> MeResponse:
    """Report what the calling token carries. Answers from the token alone, without asking the database."""
    return MeResponse.from_principal(principal)
