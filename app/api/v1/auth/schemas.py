from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from pydantic.experimental.missing_sentinel import MISSING

from app.api.v1.common import ApiModel
from app.core.auth.principal import Principal, UserPrincipal
from app.core.auth.results import CreatedClientSecret, IssuedActionToken, UserAccountWithRoles
from app.core.auth.roles import Role
from app.core.auth.tokens import CreatedAccessToken
from app.core.db.models import (
    ApplicationClient,
    ApplicationClientStatus,
    UserAccountRoleName,
    UserAccountStatus,
)

# The longest e-mail address there is (RFC 5321); the column is `text`, the limit documents the API.
MAX_EMAIL_LENGTH = 254

# A plain assignment inlines the constraints at the field; a PEP 695 alias would become its own schema.
LoginEmail = Annotated[EmailStr, StringConstraints(max_length=MAX_EMAIL_LENGTH)]


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    scope: str

    @classmethod
    def from_created_token(cls, token: CreatedAccessToken) -> AccessTokenResponse:
        return cls(
            access_token=token.access_token,
            token_type=token.token_type,
            expires_in=token.expires_in,
            scope=token.scope,
        )


class MeResponse(ApiModel):
    """What the calling token says about its bearer."""

    principal_type: str
    """Kind of principal the token was issued to; `application` for an application client, `user` for a user account."""
    client_id: str | None
    """Client ID of the application client; for a user token, the client the user logged in through."""
    scopes: list[str]
    """Scopes the token grants, sorted."""
    user_id: UUID | None
    """User account the token was issued for; `null` for an application principal."""
    party_id: UUID | None
    """CRM party the user account belongs to; `null` for an application principal."""
    roles: list[Role]
    """Roles the user holds, sorted - which views to offer. Never authorize on them, only on `scopes`."""

    @classmethod
    def from_principal(cls, principal: Principal) -> MeResponse:
        user = principal if isinstance(principal, UserPrincipal) else None
        return cls(
            principal_type=principal.principal_type,
            client_id=principal.client_id,
            scopes=sorted(principal.scopes),
            user_id=user.principal_id if user else None,
            party_id=user.party_id if user else None,
            roles=sorted(user.roles) if user else [],
        )


class ApplicationClientCreateRequest(BaseModel):
    client_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None


class ApplicationClientUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: ApplicationClientStatus | None = None


class ApplicationClientSecretCreateRequest(BaseModel):
    label: str | None = None
    expires_at: datetime | None = None


class ApplicationClientScopeGrantRequest(BaseModel):
    scopes: list[str] = Field(min_length=1)


class ApplicationClientSecretResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    label: str | None
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApplicationClientResponse(BaseModel):
    id: UUID
    client_id: str
    name: str
    description: str | None
    status: ApplicationClientStatus
    scopes: list[str]
    secrets: list[ApplicationClientSecretResponse]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, client: ApplicationClient) -> ApplicationClientResponse:
        return cls(
            id=client.id,
            client_id=client.client_id,
            name=client.name,
            description=client.description,
            status=client.status,
            scopes=sorted(grant.scope_key for grant in client.scope_grants),
            secrets=[
                ApplicationClientSecretResponse.model_validate(secret)
                for secret in sorted(client.secrets, key=lambda item: item.created_at)
            ],
            created_at=client.created_at,
            updated_at=client.updated_at,
        )


class CreatedClientSecretResponse(BaseModel):
    client_secret: str
    secret: ApplicationClientSecretResponse

    @classmethod
    def from_created_secret(cls, created_secret: CreatedClientSecret) -> CreatedClientSecretResponse:
        return cls(
            client_secret=created_secret.plaintext,
            secret=ApplicationClientSecretResponse.model_validate(created_secret.secret),
        )


# --- User accounts ---


class ActionTokenResponse(ApiModel):
    """A one-time token, to hand to the user."""

    token: str
    """The token itself. It is stored as a hash only, so this is the one time it can be read."""
    expires_at: datetime
    """When the token stops working. Issuing a new one of the same purpose invalidates this one."""

    @classmethod
    def from_issued_token(cls, issued: IssuedActionToken) -> Self:
        return cls(token=issued.plaintext, expires_at=issued.token.expires_at)


class UserAccountListItem(ApiModel):
    """A user account as one entry of the list."""

    id: UUID
    """ID of the user account; the `principal_id` of the tokens issued for it."""
    party_id: UUID
    """ID of the person party the account belongs to."""
    email: str
    """The login e-mail address, stored lowercased."""
    status: UserAccountStatus
    """`invited` until the invitation is redeemed, then `active`; `disabled` cannot log in."""
    roles: list[Role]
    """Every role the account holds, stored and derived, sorted."""
    last_login_at: datetime | None
    """When the account last logged in; `null` until the first login."""
    created_at: datetime
    """When the account was invited."""

    @classmethod
    def from_model(cls, view: UserAccountWithRoles) -> Self:
        account = view.account
        return cls(
            id=account.id,
            party_id=account.party_id,
            email=account.email,
            status=account.status,
            roles=sorted(view.roles),
            last_login_at=account.last_login_at,
            created_at=account.created_at,
        )


class UserAccountDetail(UserAccountListItem):
    """A user account with everything an administrator needs. Never the hash or the login counter."""

    locked_until: datetime | None
    """Until when the account is locked out after failed logins; `null` while it is not locked."""
    updated_at: datetime
    """When the account was last changed."""

    @classmethod
    def from_model(cls, view: UserAccountWithRoles) -> Self:
        return cls(
            **UserAccountListItem.from_model(view).model_dump(),
            locked_until=view.account.locked_until,
            updated_at=view.account.updated_at,
        )


class InvitedUserAccount(UserAccountDetail):
    """Body of `POST /users`: the invited account plus its one-time invitation token."""

    invitation: ActionTokenResponse
    """The invitation to pass on to the user. Redeemed at `POST /auth/password/redeem`."""

    @classmethod
    def from_invitation(cls, view: UserAccountWithRoles, invitation: IssuedActionToken) -> Self:
        return cls(
            **UserAccountDetail.from_model(view).model_dump(),
            invitation=ActionTokenResponse.from_issued_token(invitation),
        )


class UserAccountCreateRequest(ApiModel):
    """Body of `POST /users`: whom to invite."""

    party_id: UUID = Field(examples=["7d9f4f3e-1c2b-4a5d-9e8f-0a1b2c3d4e5f"])
    """ID of the person party the account belongs to. A company cannot hold one."""
    email: LoginEmail = Field(examples=["anna.schmidt@example.org"])
    """The login e-mail address. Stored lowercased and unique across all accounts."""
    roles: list[UserAccountRoleName] = Field(default_factory=list, examples=[["admin"]])
    """Stored roles to give the account. The other roles follow from the CRM and cannot be set."""


class UserAccountUpdateRequest(ApiModel):
    """Body of `PATCH /users/{user_id}`: only the fields that are sent change."""

    email: LoginEmail | MISSING = MISSING
    """New login e-mail address. Stored lowercased and unique across all accounts."""
    status: Literal[UserAccountStatus.ACTIVE, UserAccountStatus.DISABLED] | MISSING = MISSING
    """New status. `active` only for an account that has a password; `disabled` revokes its sessions."""


class PasswordRedeemRequest(ApiModel):
    """Body of `POST /password/redeem`: a one-time token and the password to set with it."""

    token: str = Field(examples=["sf_ua_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"])
    """The invitation or password-reset token the user was given."""
    new_password: str = Field(examples=["correct horse battery staple"])
    """The password to set: between 12 and 128 characters, no further rules."""
