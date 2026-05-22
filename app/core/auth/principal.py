import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    principal_type: str
    principal_id: uuid.UUID
    subject: str
    scopes: frozenset[str]
    client_id: str | None = None
