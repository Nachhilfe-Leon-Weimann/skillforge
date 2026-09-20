import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    """Who a validated access token speaks for.

    One shape for both principal types (ADR 0008): ``party_id``, ``session_id`` and ``roles`` are
    carried by a user token only and stay at their defaults for an application client.
    """

    principal_type: str
    principal_id: uuid.UUID
    subject: str
    scopes: frozenset[str]
    client_id: str | None = None
    party_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    roles: frozenset[str] = frozenset()
