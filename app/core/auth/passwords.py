"""Password hashing and the password policy - the one home of both (ADR 0008, decision B).

The policy is checked in the service rather than in a request schema, so "too short" reads the
same wherever a password is set. Hashing reuses ``PasswordHash.recommended()``, the Argon2 setup
that already hashes client secrets in ``secrets.py``.
"""

from pwdlib import PasswordHash
from pwdlib import exceptions as pwdlib_exceptions

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

_PASSWORD_HASH = PasswordHash.recommended()

DUMMY_PASSWORD_HASH = _PASSWORD_HASH.hash("no account uses this password")
"""A real hash of a password no account has, verified against when no account matched.

Keeps the timing of "unknown e-mail address" close to "wrong password", so the two cannot be told
apart by it (spec: no enumeration). Computed once, at import.
"""


def meets_password_policy(password: str) -> bool:
    """Whether ``password`` may be stored: 12 to 128 characters, no composition rules."""
    return MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH


def hash_password(password: str) -> str:
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Whether ``password`` produced ``password_hash``; a hash of an unknown format is a mismatch."""
    try:
        return _PASSWORD_HASH.verify(password, password_hash)
    except pwdlib_exceptions.UnknownHashError, ValueError, TypeError:
        return False
