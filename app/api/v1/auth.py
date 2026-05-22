from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthSettings,
    CreatedAccessToken,
    InvalidClientCredentialsError,
    InvalidClientScopeError,
    issue_client_token,
)
from app.core.auth.dependencies import get_auth_settings
from app.core.db.dependencies import get_db_session

router = APIRouter(prefix="/auth", tags=["auth"])

IssueClientToken = Callable[..., Awaitable[CreatedAccessToken]]


def get_issue_client_token() -> IssueClientToken:
    return issue_client_token


@router.post("/token")
async def create_token(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
    issue_token: Annotated[IssueClientToken, Depends(get_issue_client_token)],
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
    scope: Annotated[str | None, Form()] = None,
):
    if grant_type != "client_credentials":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported grant_type",
        )

    try:
        token: CreatedAccessToken = await issue_token(
            session,
            settings,
            client_id=client_id,
            client_secret=client_secret,
            requested_scopes=scope,
        )
    except InvalidClientCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except InvalidClientScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid requested scope",
        ) from exc

    return {
        "access_token": token.access_token,
        "token_type": token.token_type,
        "expires_in": token.expires_in,
        "scope": token.scope,
    }
