from enum import StrEnum


class Scope(StrEnum):
    """Defines the scopes for authentication."""

    BOT_READ = "bot:read"
    BOT_WRITE = "bot:write"
    AUTH_CLIENTS_MANAGE = "auth:clients:manage"


DEFAULT_SCOPES: dict[Scope, str] = {
    Scope.BOT_READ: "Read bot API surface.",
    Scope.BOT_WRITE: "Write bot API surface.",
    Scope.AUTH_CLIENTS_MANAGE: "Manage application clients.",
}
