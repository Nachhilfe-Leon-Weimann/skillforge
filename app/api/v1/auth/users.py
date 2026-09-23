"""The admin surface of user accounts plus the route that redeems a one-time token.

Everything under `/users` is guarded by `auth:users:manage`. `POST /password/redeem` is guarded by
``require_application`` with `auth:users:login`: a user token must never redeem a token, no matter
what it carries.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Security, status
from pydantic import Field

from app.api.v1.common import DBSession, Page, PageParams, error_responses
from app.core.auth import AuthSettings, Principal, Scope, require_application, require_scopes
from app.core.auth.dependencies import get_auth_settings
from app.core.auth.services import users as users_service
from app.core.auth.services.errors import (
    AccountPartyNotAPersonError,
    AccountPartyNotFoundError,
    InvalidActionTokenError,
    UserAccountAlreadyExistsError,
    UserAccountNotFoundError,
    UserAccountStateError,
    UserEmailAlreadyInUseError,
    UserRoleNotFoundError,
    WeakPasswordError,
)
from app.core.auth.services.users import MAX_EMAIL_LENGTH
from app.core.db.models import UserAccountRoleName, UserAccountStatus, UserActionTokenPurpose

from .schemas import (
    ActionTokenResponse,
    InvitedUserAccount,
    PasswordRedeemRequest,
    UserAccountCreateRequest,
    UserAccountDetail,
    UserAccountListItem,
    UserAccountUpdateRequest,
)

router = APIRouter(prefix="/users")
password_router = APIRouter(prefix="/password")

ManageUsers = Annotated[Principal, require_scopes(Scope.AUTH_USERS_MANAGE)]
# The redeem route acts on behalf of a user, so it is a client's to call. One declaration: the scope
# reaches the nested ``get_current_principal`` - and with it the operation's `security` and its 403 -
# and ``require_application`` refuses a user token whatever it carries.
LoginClient = Annotated[Principal, Security(require_application, scopes=[Scope.AUTH_USERS_LOGIN])]
AuthConfig = Annotated[AuthSettings, Depends(get_auth_settings)]

UserId = Annotated[
    uuid.UUID,
    Path(description="ID of the user account.", examples=["3f2b8c1e-5a4d-4e6f-8a9b-0c1d2e3f4a5b"]),
]

StoredRole = Annotated[
    UserAccountRoleName,
    Path(
        description="A role that is stored on the account; the other roles follow from the CRM.",
        examples=[UserAccountRoleName.ADMIN],
    ),
]


class UserAccountListParams(PageParams):
    """Filters of `GET /users`. They never fail: a combination nothing matches is an empty page."""

    status: UserAccountStatus | None = Field(None, description="Only accounts in this status.")
    party_id: uuid.UUID | None = Field(None, description="Only the account of this party; at most one.")
    email: str | None = Field(
        None,
        max_length=MAX_EMAIL_LENGTH,
        description="Only the account with exactly this e-mail address, compared lowercased.",
    )


type UserAccountListQuery = Annotated[UserAccountListParams, Query()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(
        AccountPartyNotFoundError,
        AccountPartyNotAPersonError,
        UserAccountAlreadyExistsError,
        UserEmailAlreadyInUseError,
    ),
)
async def invite_user(
    request: UserAccountCreateRequest,
    session: DBSession,
    settings: AuthConfig,
    principal: ManageUsers,
) -> InvitedUserAccount:
    """Invite a person party: create the account and issue its invitation token.

    The token is in the response and nowhere else - Forge stores only its hash and cannot show it
    again. Pass it to the user; they set their password at `POST /auth/password/redeem`.
    """
    view, invitation = await users_service.invite_user_account(
        session,
        settings,
        party_id=request.party_id,
        email=request.email,
        roles=request.roles,
        actor=_actor(principal),
    )
    return InvitedUserAccount.from_invitation(view, invitation)


@router.get("", dependencies=[require_scopes(Scope.AUTH_USERS_MANAGE)])
async def list_users(params: UserAccountListQuery, session: DBSession) -> Page[UserAccountListItem]:
    """List user accounts, ordered by e-mail address."""
    accounts, total = await users_service.list_user_accounts(session, **params.model_dump())
    return Page.of([UserAccountListItem.from_model(view) for view in accounts], total=total, params=params)


@router.get(
    "/{user_id}",
    dependencies=[require_scopes(Scope.AUTH_USERS_MANAGE)],
    responses=error_responses(UserAccountNotFoundError),
)
async def get_user(user_id: UserId, session: DBSession) -> UserAccountDetail:
    """Read a single user account, with its stored and derived roles."""
    return UserAccountDetail.from_model(await users_service.load_user_account(session, user_id))


@router.patch(
    "/{user_id}",
    responses=error_responses(UserAccountNotFoundError, UserEmailAlreadyInUseError, UserAccountStateError),
)
async def update_user(
    user_id: UserId,
    request: UserAccountUpdateRequest,
    session: DBSession,
    principal: ManageUsers,
) -> UserAccountDetail:
    """Change a user account. Only the fields that are sent change; an empty body changes nothing.

    Disabling an account revokes its sessions right away; the access tokens already handed out
    expire within 15 minutes.
    """
    view = await users_service.update_user_account(session, user_id, **request.model_dump(), actor=_actor(principal))
    return UserAccountDetail.from_model(view)


@router.put("/{user_id}/roles/{role}", responses=error_responses(UserAccountNotFoundError))
async def add_user_role(
    user_id: UserId, role: StoredRole, session: DBSession, principal: ManageUsers
) -> UserAccountDetail:
    """Give the account a stored role. Idempotent: holding it already changes nothing."""
    view = await users_service.add_user_role(session, user_id, role=role, actor=_actor(principal))
    return UserAccountDetail.from_model(view)


@router.delete(
    "/{user_id}/roles/{role}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(UserAccountNotFoundError, UserRoleNotFoundError),
)
async def remove_user_role(user_id: UserId, role: StoredRole, session: DBSession, principal: ManageUsers) -> None:
    """Take a stored role away. The roles the CRM derives cannot be removed here."""
    await users_service.remove_user_role(session, user_id, role=role, actor=_actor(principal))


@router.post(
    "/{user_id}/invitation",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(UserAccountNotFoundError, UserAccountStateError),
)
async def issue_invitation(
    user_id: UserId, session: DBSession, settings: AuthConfig, principal: ManageUsers
) -> ActionTokenResponse:
    """Issue a fresh invitation. Refused once the account has a password - reset it instead.

    Earlier unused invitations stop working.
    """
    issued = await users_service.issue_action_token(
        session,
        settings,
        user_id=user_id,
        purpose=UserActionTokenPurpose.INVITATION,
        actor=_actor(principal),
    )
    return ActionTokenResponse.from_issued_token(issued)


@router.post(
    "/{user_id}/password-reset",
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(UserAccountNotFoundError, UserAccountStateError),
)
async def issue_password_reset(
    user_id: UserId, session: DBSession, settings: AuthConfig, principal: ManageUsers
) -> ActionTokenResponse:
    """Issue a password reset. Refused for an account that has no password - invite it instead.

    Earlier unused resets stop working; nothing else changes until the token is redeemed.
    """
    issued = await users_service.issue_action_token(
        session,
        settings,
        user_id=user_id,
        purpose=UserActionTokenPurpose.PASSWORD_RESET,
        actor=_actor(principal),
    )
    return ActionTokenResponse.from_issued_token(issued)


@router.delete(
    "/{user_id}/sessions",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(UserAccountNotFoundError),
)
async def revoke_user_sessions(user_id: UserId, session: DBSession, principal: ManageUsers) -> None:
    """Revoke every session of the account, so no refresh token of it works any more."""
    await users_service.revoke_user_sessions(session, user_id, actor=_actor(principal))


@password_router.post(
    "/redeem",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses(InvalidActionTokenError, WeakPasswordError),
)
async def redeem_password(request: PasswordRedeemRequest, session: DBSession, principal: LoginClient) -> None:
    """Set a password with an invitation or a password-reset token.

    An unknown, used, expired or replaced token answers the same body, so a caller cannot tell
    which of the four it was. Redeeming a reset revokes every session of the account.
    """
    await users_service.redeem_action_token(
        session,
        plaintext=request.token,
        new_password=request.new_password,
        actor=_actor(principal),
    )


def _actor(principal: Principal) -> str:
    """Who asked, as it is recorded on the token row and in the audit entry."""
    return f"{principal.principal_type}:{principal.principal_id}"
