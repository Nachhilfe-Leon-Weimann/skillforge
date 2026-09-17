from app.core.errors import ConflictError, NotFoundError


class ClientCredentialsError(ValueError):
    """Raised when client credentials cannot be exchanged for an access token.

    Deliberately outside the error taxonomy: the OAuth2 statuses and codes of these errors (400/401,
    ``invalid_client``, ``invalid_scope``) are declared by the auth API in ``app/api/v1/auth/errors.py``.
    """


class InvalidClientCredentialsError(ClientCredentialsError):
    """Raised for unknown clients, disabled clients, or invalid client secrets."""


class InvalidClientScopeError(ClientCredentialsError):
    """Raised when requested scopes are unknown, inactive, or not granted to the client."""


class ApplicationClientManagementError(ValueError):
    """Raised when application client management cannot be completed."""


class ApplicationClientAlreadyExistsError(ApplicationClientManagementError, ConflictError):
    """Raised when creating a client with an existing client_id."""

    message = "Application client already exists"


class ApplicationClientNotFoundError(ApplicationClientManagementError, NotFoundError):
    """Raised when an application client cannot be found."""

    message = "Application client not found"


class ApplicationClientSecretNotFoundError(ApplicationClientManagementError, NotFoundError):
    """Raised when an application client secret cannot be found."""

    message = "Application client secret not found"


class ApplicationClientScopeGrantNotFoundError(ApplicationClientManagementError, NotFoundError):
    """Raised when a scope grant cannot be found."""

    message = "Application client scope grant not found"
