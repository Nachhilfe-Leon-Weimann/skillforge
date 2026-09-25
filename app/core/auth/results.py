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
    """A one-time token as it leaves the service: the only place its plaintext exists.

    ``token`` holds only the digest, so the plaintext travels next to it - straight into the response
    or the terminal, never into a log or an audit entry.
    """

    plaintext: str
    token: UserActionToken


@dataclass(frozen=True)
class UserAccountWithRoles:
    """An account together with every role it holds: the stored ones plus the ones the CRM derives."""

    account: UserAccount
    roles: frozenset[Role]


@dataclass(frozen=True)
class BootstrappedAdminAccount:
    """What ``bootstrap_admin_account`` did: the account, whether it created it, and the token it issued."""

    account: UserAccount
    created_account: bool
    issued: IssuedActionToken
