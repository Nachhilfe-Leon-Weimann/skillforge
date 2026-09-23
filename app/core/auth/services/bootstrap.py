import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import (
    ApplicationClient,
    ApplicationClientStatus,
    UserAccountRoleName,
    UserActionTokenPurpose,
)

from ..audit import AuditEventType, write_auth_audit_log
from ..config import AuthSettings
from ..results import BootstrappedAdminAccount, BootstrappedApplicationClient
from ..scopes import Scope, parse_scopes
from .accounts import find_user_account_by_party
from .action_tokens import issue_action_token
from .clients import find_application_client
from .scopes import grant_client_scopes, seed_default_scopes
from .secrets import client_has_usable_secret, create_client_secret
from .users import add_user_role, invite_user_account

BOOTSTRAP_ACTOR = "cli"
"""What the operator commands record as the issuer, where a request records its principal."""


async def bootstrap_application_client(
    session: AsyncSession,
    *,
    client_id: str,
    name: str,
    description: str | None,
    scopes: Iterable[Scope | str],
) -> BootstrappedApplicationClient:
    await seed_default_scopes(session)

    client = await find_application_client(session, client_id)
    created_client = False
    if client is None:
        client = ApplicationClient(
            client_id=client_id,
            name=name,
            description=description,
            status=ApplicationClientStatus.ACTIVE,
        )
        session.add(client)
        await session.flush()
        created_client = True
        await write_auth_audit_log(
            session,
            principal_type="application",
            principal_id=client.id,
            event_type=AuditEventType.APPLICATION_CLIENT_CREATED,
            success=True,
            detail=f"Created application client {client.client_id}",
        )
    else:
        client.name = name
        client.description = description
        client.status = ApplicationClientStatus.ACTIVE

    requested_scope_keys = parse_scopes(scopes)
    granted_scope_keys = await grant_client_scopes(session, client=client, scope_keys=requested_scope_keys)

    created_secret = None
    if not await client_has_usable_secret(session, client_id=client.id, now=datetime.now(UTC)):
        created_secret = await create_client_secret(
            session,
            application_client_id=client.id,
            label="bootstrap",
        )

    await session.flush()
    return BootstrappedApplicationClient(
        client=client,
        created_client=created_client,
        created_secret=created_secret,
        granted_scopes=granted_scope_keys,
    )


async def bootstrap_admin_account(
    session: AsyncSession,
    settings: AuthSettings,
    *,
    party_id: uuid.UUID,
    email: str,
) -> BootstrappedAdminAccount:
    """Make sure the party has a user account holding the ``admin`` role, and hand out a way in.

    Inviting needs an admin, so the first one cannot be invited (decision O). Idempotent: run
    again it keeps the account and its e-mail address, makes sure it still holds the role, and
    issues a fresh invitation only while the account has no password - once it has one, the
    operator resets it through the API instead.
    """
    existing = await find_user_account_by_party(session, party_id)
    if existing is None:
        created = await invite_user_account(
            session,
            settings,
            party_id=party_id,
            email=email,
            roles=[UserAccountRoleName.ADMIN],
            actor=BOOTSTRAP_ACTOR,
        )
        return BootstrappedAdminAccount(
            account=created.view.account, created_account=True, invitation=created.invitation
        )

    view = await add_user_role(session, existing.id, role=UserAccountRoleName.ADMIN, actor=BOOTSTRAP_ACTOR)
    invitation = None
    if view.account.password_hash is None:
        invitation = await issue_action_token(
            session,
            settings,
            user_id=existing.id,
            purpose=UserActionTokenPurpose.INVITATION,
            actor=BOOTSTRAP_ACTOR,
        )

    return BootstrappedAdminAccount(account=view.account, created_account=False, invitation=invitation)
