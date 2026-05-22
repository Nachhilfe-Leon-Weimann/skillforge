from dataclasses import dataclass

from app.core.db.models import ApplicationClient, ApplicationClientSecret


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
