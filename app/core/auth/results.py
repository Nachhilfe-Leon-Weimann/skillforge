from dataclasses import dataclass

from app.core.db.models import ApplicationClient, ApplicationClientSecret, UserAccount, UserActionToken

from .roles import Role


@dataclass(frozen=True)
class CreatedClientSecret:
    plaintext: str
    secret: ApplicationClientSecret


@dataclass(frozen=True)
class BootstrappedApplicationClient:
    client: ApplicationClient
    created_client: bool
    created_secret: CreatedClientSecret | None
    granted_scopes: frozenset[str]


@dataclass(frozen=True)
class IssuedActionToken:
    """A one-time token as it leaves the service: the plaintext exists here and nowhere else.

    ``token`` holds only its hash, so the plaintext has to travel next to it - straight into the
    response that issues it, never into a log or an audit entry.
    """

    plaintext: str
    token: UserActionToken


@dataclass(frozen=True)
class UserAccountWithRoles:
    """An account together with every role it holds: the stored ones plus the CRM-derived ones."""

    account: UserAccount
    roles: frozenset[Role]


@dataclass(frozen=True)
class CreatedUserAccount:
    """An invited account and its invitation: the one time the invitation's plaintext exists."""

    view: UserAccountWithRoles
    invitation: IssuedActionToken


@dataclass(frozen=True)
class BootstrappedAdminAccount:
    """What ``bootstrap_admin`` did: the account, whether it had to create it, and a fresh
    invitation while the account has no password."""

    account: UserAccount
    created_account: bool
    invitation: IssuedActionToken | None
