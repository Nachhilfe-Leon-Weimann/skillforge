import secrets as random_secrets

from pwdlib import PasswordHash
from pwdlib import exceptions as pwdlib_exceptions

SECRET_PREFIX = "sf_live_"
SECRET_BYTES = 32

_PASSWORD_HASH = PasswordHash.recommended()


def generate_client_secret(*, prefix: str = SECRET_PREFIX, nbytes: int = SECRET_BYTES) -> str:
    if nbytes <= 0:
        raise ValueError("nbytes must be positive")

    return f"{prefix}{random_secrets.token_urlsafe(nbytes)}"


def hash_client_secret(secret: str) -> str:
    if not secret:
        raise ValueError("secret must not be empty")

    return _PASSWORD_HASH.hash(secret)


def verify_client_secret(secret: str, secret_hash: str) -> bool:
    try:
        return _PASSWORD_HASH.verify(secret, secret_hash)
    except (pwdlib_exceptions.UnknownHashError, ValueError, TypeError):
        return False
