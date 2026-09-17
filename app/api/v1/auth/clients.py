from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.common import error_responses
from app.core.auth import (
    ApplicationClientAlreadyExistsError,
    ApplicationClientNotFoundError,
    ApplicationClientScopeGrantNotFoundError,
    ApplicationClientSecretNotFoundError,
    InvalidClientScopeError,
    Principal,
    Scope,
    create_application_client,
    create_application_client_secret,
    get_application_client,
    grant_application_client_scopes,
    list_application_clients,
    revoke_application_client_scope,
    revoke_application_client_secret,
    update_application_client,
)
from app.core.auth.dependencies import require_scopes
from app.core.db.dependencies import get_db_session

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


@router.get("", response_model=list[ApplicationClientResponse])
async def read_application_clients(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> list[ApplicationClientResponse]:
    clients = await list_application_clients(session)
    return [ApplicationClientResponse.from_model(client) for client in clients]


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
        client = await grant_application_client_scopes(session, client_id=client_id, scopes=request.scopes)
    except InvalidClientScopeError as exc:
        # Local mapping: 400 is outside STATUS_BY_ERROR. Raised (not returned), so scopes granted
        # before the invalid one are rolled back.
        raise INVALID_SCOPE.exception() from exc

    return ApplicationClientResponse.from_model(client)


@router.delete(
    "/{client_id}/scopes/{scope_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(ApplicationClientNotFoundError, ApplicationClientScopeGrantNotFoundError),
)
async def revoke_application_client_scope_endpoint(
    client_id: str,
    scope_key: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> None:
    await revoke_application_client_scope(session, client_id=client_id, scope_key=scope_key)
