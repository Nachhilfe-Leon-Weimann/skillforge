"""The secret primitives of the auth core: generate, hash, verify and digest - their one home.

Two hash families, chosen by what they protect:

- Argon2 (``hash_secret`` / ``verify_secret``) for secrets that are stored for a long time and only
  ever verified: client secrets and passwords. Salted and slow on purpose, so a hash cannot serve as
  a lookup key.
- SHA-256 (``digest``) for opaque tokens that are looked up by their hash: action tokens and refresh
  tokens. They carry full entropy, so a fast, deterministic hash is enough.
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
    """Return the Argon2 hash of a client secret or a password."""
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
    """Return the stored form of an opaque token: its SHA-256 hex digest, deterministic so it can be looked up.

    Any string has a digest, even one with a lone surrogate (valid JSON, not valid UTF-8): such a token was
    never generated, so its digest matches nothing.
    """
    return hashlib.sha256(token.encode(errors="surrogatepass")).hexdigest()
