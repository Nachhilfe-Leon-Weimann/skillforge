from enum import StrEnum
from typing import Self


class Scope(StrEnum):
    """Defines the scopes for authentication.

    Each member is declared as ``(value, description)``. The value is the wire format; the
    description feeds the seeded ``PermissionScope`` rows and the OpenAPI security scheme, so a
    scope without a description cannot be declared.
    """

    description: str

    def __new__(cls, value: str, description: str) -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.description = description
        return member

    BOT_READ = "bot:read", "Read bot API surface."
    BOT_WRITE = "bot:write", "Write bot API surface."
    AUTH_CLIENTS_MANAGE = "auth:clients:manage", "Manage application clients."
