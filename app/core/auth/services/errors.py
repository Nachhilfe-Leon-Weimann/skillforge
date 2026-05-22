class ClientCredentialsError(ValueError):
    """Raised when client credentials cannot be exchanged for an access token."""


class InvalidClientCredentialsError(ClientCredentialsError):
    """Raised for unknown clients, disabled clients, or invalid client secrets."""


class InvalidClientScopeError(ClientCredentialsError):
    """Raised when requested scopes are unknown, inactive, or not granted to the client."""


class ApplicationClientManagementError(ValueError):
    """Raised when application client management cannot be completed."""


class ApplicationClientAlreadyExistsError(ApplicationClientManagementError):
    """Raised when creating a client with an existing client_id."""


class ApplicationClientNotFoundError(ApplicationClientManagementError):
    """Raised when an application client cannot be found."""


class ApplicationClientSecretNotFoundError(ApplicationClientManagementError):
    """Raised when an application client secret cannot be found."""


class ApplicationClientScopeGrantNotFoundError(ApplicationClientManagementError):
    """Raised when a scope grant cannot be found."""
