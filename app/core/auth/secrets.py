"""The secret primitives of the auth core: generate, hash, verify and digest - their one home.

Two hash families, chosen by what they protect:

- Argon2 (``hash_secret`` / ``verify_secret``) for secrets that are stored for a long time and
  only ever verified: passwords and client secrets. Salted and slow on purpose, so a hash cannot
  serve as a lookup key.
- SHA-256 (``digest``) for opaque one-time tokens that are looked up by their hash: action tokens,
  and the refresh tokens of the ``refresh_token`` grant. They carry full entropy, so a fast,
  deterministic hash is enough.
"""

import hashlib
import secrets as random_secrets

from pwdlib import PasswordHash
from pwdlib import exceptions as pwdlib_exceptions

SECRET_PREFIX = "sf_live_"
"""Prefix of a client secret."""
SECRET_BYTES = 32

_PASSWORD_HASH = PasswordHash.recommended()


def generate_secret(prefix: str, nbytes: int = SECRET_BYTES) -> str:
    """Return ``prefix`` followed by ``nbytes`` random bytes, URL-safe encoded."""
    if nbytes <= 0:
        raise ValueError("nbytes must be positive")

    return f"{prefix}{random_secrets.token_urlsafe(nbytes)}"


def hash_secret(secret: str) -> str:
    if not secret:
        raise ValueError("secret must not be empty")

    return _PASSWORD_HASH.hash(secret)


def verify_secret(secret: str, secret_hash: str) -> bool:
    """Whether ``secret`` produced ``secret_hash``; a hash of an unknown format is a mismatch."""
    try:
        return _PASSWORD_HASH.verify(secret, secret_hash)
    except pwdlib_exceptions.UnknownHashError, ValueError, TypeError:
        return False


def digest(token: str) -> str:
    """The stored form of an opaque token: SHA-256 hex, deterministic so it can be looked up."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
