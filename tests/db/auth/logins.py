"""Login clients for the tests of the person grants: shared by the fixtures in ``conftest.py`` and the tests that
commit their own setup."""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Scope
from app.core.auth.roles import Role, scopes_for
from app.core.db.models import GrantMode
from app.services.auth import bootstrap_application_client

PORTAL_DELEGATED_SCOPES: frozenset[Scope] = scopes_for([Role.ADMIN])
"""What the portal may do for a person at most: everything a role grants (see "Operating without a portal")."""


@dataclass(frozen=True)
class LoginClientCredentials:
    """A real application client with a secret, as `POST /token` authenticates it."""

    id: UUID
    client_id: str
    client_secret: str

    @property
    def basic(self) -> tuple[str, str]:
        """The credentials for HTTP Basic authentication."""
        return self.client_id, self.client_secret

    @property
    def form(self) -> dict[str, str]:
        """The credentials as form fields."""
        return {"client_id": self.client_id, "client_secret": self.client_secret}


async def bootstrap_login_client(
    session: AsyncSession,
    *,
    client_id: str | None = None,
    application: Iterable[Scope] = (Scope.AUTH_USERS_LOGIN,),
    delegated: Iterable[Scope] = PORTAL_DELEGATED_SCOPES,
) -> LoginClientCredentials:
    """Create a client with a secret and grants in both modes; by default a login client with the portal's ceiling."""
    client_id = client_id or f"portal-{uuid4().hex[:8]}"
    created = await bootstrap_application_client(session, client_id=client_id, scopes=application)
    await bootstrap_application_client(session, client_id=client_id, scopes=delegated, mode=GrantMode.DELEGATED)
    assert created.created_secret is not None
    return LoginClientCredentials(
        id=created.client.id, client_id=client_id, client_secret=created.created_secret.plaintext
    )
