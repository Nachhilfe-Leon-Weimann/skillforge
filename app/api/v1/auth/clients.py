from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import Page, PageQuery, error_responses
from app.core.auth import Principal, Scope
from app.core.auth.dependencies import require_scopes
from app.core.db.dependencies import get_db_session
from app.core.db.models import GrantMode
from app.services.auth import (
    ApplicationClientAlreadyExistsError,
    ApplicationClientNotFoundError,
    ApplicationClientScopeGrantNotFoundError,
    ApplicationClientSecretNotFoundError,
    InvalidClientScopeError,
    create_application_client,
    create_application_client_secret,
    get_application_client,
    grant_application_client_scopes,
    list_application_clients,
    revoke_application_client_scope,
    revoke_application_client_secret,
    update_application_client,
)

from .errors import INVALID_SCOPE
from .schemas import (
    ApplicationClientCreateRequest,
    ApplicationClientResponse,
    ApplicationClientScopeGrantRequest,
    ApplicationClientSecretCreateRequest,
    ApplicationClientUpdateRequest,
    CreatedClientSecretResponse,
)

router = APIRouter(prefix="/clients")

ManageAuthClients = Annotated[Principal, require_scopes(Scope.AUTH_CLIENTS_MANAGE)]

ScopeGrantMode = Annotated[
    GrantMode,
    Path(
        description="Mode of the grant: `application` (for the client itself) or `delegated` (the ceiling for people).",
        examples=[GrantMode.DELEGATED],
    ),
]
ScopeKey = Annotated[str, Path(description="Scope of the grant.", examples=["crm:read"])]


@router.get("")
async def read_application_clients(
    params: PageQuery,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> Page[ApplicationClientResponse]:
    """List application clients ordered by `client_id`."""
    clients, total = await list_application_clients(session, **params.model_dump())
    return Page.of([ApplicationClientResponse.from_model(client) for client in clients], total=total, params=params)


@router.post(
    "",
    response_model=ApplicationClientResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(ApplicationClientAlreadyExistsError),
)
async def create_application_client_endpoint(
    request: ApplicationClientCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    client = await create_application_client(
        session,
        client_id=request.client_id,
        name=request.name,
        description=request.description,
    )
    return ApplicationClientResponse.from_model(client)


@router.get(
    "/{client_id}",
    response_model=ApplicationClientResponse,
    responses=error_responses(ApplicationClientNotFoundError),
)
async def read_application_client(
    client_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    client = await get_application_client(session, client_id=client_id)
    return ApplicationClientResponse.from_model(client)


@router.patch(
    "/{client_id}",
    response_model=ApplicationClientResponse,
    responses=error_responses(ApplicationClientNotFoundError),
)
async def update_application_client_endpoint(
    client_id: str,
    request: ApplicationClientUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    client = await update_application_client(
        session,
        client_id=client_id,
        name=request.name,
        description=request.description,
        update_description="description" in request.model_fields_set,
        status=request.status,
    )
    return ApplicationClientResponse.from_model(client)


@router.post(
    "/{client_id}/secrets",
    response_model=CreatedClientSecretResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(ApplicationClientNotFoundError),
)
async def create_application_client_secret_endpoint(
    client_id: str,
    request: ApplicationClientSecretCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> CreatedClientSecretResponse:
    created_secret = await create_application_client_secret(
        session,
        client_id=client_id,
        label=request.label,
        expires_at=request.expires_at,
    )
    return CreatedClientSecretResponse.from_created_secret(created_secret)


@router.delete(
    "/{client_id}/secrets/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(ApplicationClientNotFoundError, ApplicationClientSecretNotFoundError),
)
async def revoke_application_client_secret_endpoint(
    client_id: str,
    secret_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> None:
    await revoke_application_client_secret(session, client_id=client_id, secret_id=secret_id)


@router.post(
    "/{client_id}/scopes",
    response_model=ApplicationClientResponse,
    responses=error_responses(ApplicationClientNotFoundError, INVALID_SCOPE),
)
async def grant_application_client_scopes_endpoint(
    client_id: str,
    request: ApplicationClientScopeGrantRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    try:
        client = await grant_application_client_scopes(
            session, client_id=client_id, scopes=request.scopes, mode=request.mode
        )
    except InvalidClientScopeError as exc:
        # Local mapping: 400 is outside STATUS_BY_ERROR. Raised, not returned: a refused grant writes
        # nothing that has to survive the rollback.
        raise INVALID_SCOPE.exception() from exc

    return ApplicationClientResponse.from_model(client)


@router.delete(
    "/{client_id}/scopes/{mode}/{scope_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(ApplicationClientNotFoundError, ApplicationClientScopeGrantNotFoundError),
)
async def revoke_application_client_scope_endpoint(
    client_id: str,
    mode: ScopeGrantMode,
    scope_key: ScopeKey,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> None:
    await revoke_application_client_scope(session, client_id=client_id, scope_key=scope_key, mode=mode)
