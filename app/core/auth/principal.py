import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from .roles import Role


class PrincipalType(StrEnum):
    """Whom an access token speaks for - the value of its ``principal_type`` claim."""

    APPLICATION = "application"
    USER = "user"


@dataclass(frozen=True, kw_only=True)
class Principal(ABC):
    """Who a validated access token speaks for (ADR 0008).

    Never one of its own: a token speaks for an ``ApplicationPrincipal`` or a ``UserPrincipal``.
    Code that needs one of the two narrows with ``isinstance`` or ``match`` - the type then
    guarantees what the principal carries, instead of an optional field that has to be checked.
    """

    principal_type: ClassVar[PrincipalType]

    principal_id: uuid.UUID
    client_id: str
    """The client the token was issued to; for a user token, the client the user logged in through."""
    scopes: frozenset[str]

    @property
    @abstractmethod
    def subject(self) -> str:
        """The token's ``sub`` claim, derived from the principal and checked against it on validation."""

    @property
    def actor(self) -> str:
        """How the principal is recorded as the one who asked: ``<principal_type>:<principal_id>``."""
        return f"{self.principal_type}:{self.principal_id}"


@dataclass(frozen=True, kw_only=True)
class ApplicationPrincipal(Principal):
    """An application client acting for itself (``client_credentials``); ``principal_id`` is the client row."""

    principal_type = PrincipalType.APPLICATION

    @property
    def subject(self) -> str:
        return f"app:{self.client_id}"


@dataclass(frozen=True, kw_only=True)
class UserPrincipal(Principal):
    """A user account, acting through the client it logged in with; ``principal_id`` is the account row."""

    principal_type = PrincipalType.USER

    party_id: uuid.UUID
    session_id: uuid.UUID
    roles: frozenset[Role] = frozenset()
    """Which views to offer. Informational only: Forge authorizes by scope and never branches on a role."""

    @property
    def subject(self) -> str:
        return f"user:{self.principal_id}"
