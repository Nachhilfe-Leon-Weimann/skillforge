from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    ApplicationClientAlreadyExistsError,
    ApplicationClientNotFoundError,
    ApplicationClientScopeGrantNotFoundError,
    ApplicationClientSecretNotFoundError,
    InvalidClientScopeError,
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

from .schemas import (
    ApplicationClientCreateRequest,
    ApplicationClientResponse,
    ApplicationClientScopeGrantRequest,
    ApplicationClientSecretCreateRequest,
    ApplicationClientUpdateRequest,
    CreatedClientSecretResponse,
)

router = APIRouter(prefix="/clients")

ManageAuthClients = Annotated[object, Depends(require_scopes(Scope.AUTH_CLIENTS_MANAGE))]


@router.get("", response_model=list[ApplicationClientResponse])
async def read_application_clients(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> list[ApplicationClientResponse]:
    clients = await list_application_clients(session)
    return [ApplicationClientResponse.from_model(client) for client in clients]


@router.post("", response_model=ApplicationClientResponse, status_code=status.HTTP_201_CREATED)
async def create_application_client_endpoint(
    request: ApplicationClientCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    try:
        client = await create_application_client(
            session,
            client_id=request.client_id,
            name=request.name,
            description=request.description,
        )
    except ApplicationClientAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Application client already exists",
        ) from exc

    return ApplicationClientResponse.from_model(client)


@router.get("/{client_id}", response_model=ApplicationClientResponse)
async def read_application_client(
    client_id: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    try:
        client = await get_application_client(session, client_id=client_id)
    except ApplicationClientNotFoundError as exc:
        raise _client_not_found() from exc

    return ApplicationClientResponse.from_model(client)


@router.patch("/{client_id}", response_model=ApplicationClientResponse)
async def update_application_client_endpoint(
    client_id: str,
    request: ApplicationClientUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    try:
        client = await update_application_client(
            session,
            client_id=client_id,
            name=request.name,
            description=request.description,
            update_description="description" in request.model_fields_set,
            status=request.status,
        )
    except ApplicationClientNotFoundError as exc:
        raise _client_not_found() from exc

    return ApplicationClientResponse.from_model(client)


@router.post(
    "/{client_id}/secrets",
    response_model=CreatedClientSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_application_client_secret_endpoint(
    client_id: str,
    request: ApplicationClientSecretCreateRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> CreatedClientSecretResponse:
    try:
        created_secret = await create_application_client_secret(
            session,
            client_id=client_id,
            label=request.label,
            expires_at=request.expires_at,
        )
    except ApplicationClientNotFoundError as exc:
        raise _client_not_found() from exc

    return CreatedClientSecretResponse.from_created_secret(created_secret)


@router.delete("/{client_id}/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_application_client_secret_endpoint(
    client_id: str,
    secret_id: UUID,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> None:
    try:
        await revoke_application_client_secret(session, client_id=client_id, secret_id=secret_id)
    except ApplicationClientNotFoundError as exc:
        raise _client_not_found() from exc
    except ApplicationClientSecretNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application client secret not found",
        ) from exc


@router.post("/{client_id}/scopes", response_model=ApplicationClientResponse)
async def grant_application_client_scopes_endpoint(
    client_id: str,
    request: ApplicationClientScopeGrantRequest,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> ApplicationClientResponse:
    try:
        client = await grant_application_client_scopes(session, client_id=client_id, scopes=request.scopes)
    except ApplicationClientNotFoundError as exc:
        raise _client_not_found() from exc
    except InvalidClientScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid requested scope",
        ) from exc

    return ApplicationClientResponse.from_model(client)


@router.delete("/{client_id}/scopes/{scope_key}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_application_client_scope_endpoint(
    client_id: str,
    scope_key: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    _: ManageAuthClients,
) -> None:
    try:
        await revoke_application_client_scope(session, client_id=client_id, scope_key=scope_key)
    except ApplicationClientNotFoundError as exc:
        raise _client_not_found() from exc
    except ApplicationClientScopeGrantNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Application client scope grant not found",
        ) from exc


def _client_not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Application client not found",
    )
