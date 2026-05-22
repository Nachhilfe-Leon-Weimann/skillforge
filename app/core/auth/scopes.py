from enum import StrEnum


class Scope(StrEnum):
    """Defines the scopes for authentication."""

    BOT_READ = "bot:read"
    BOT_WRITE = "bot:write"
    AUTH_CLIENTS_MANAGE = "auth:clients:manage"
