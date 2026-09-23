"""The password policy - its one home (ADR 0008, decision B).

The policy is checked in the service rather than in a request schema, so "too short" reads the
same wherever a password is set. Hashing is not done here: a password is hashed like a client
secret, with ``hash_secret`` / ``verify_secret`` in ``secrets.py``.
"""

from functools import cache

from .secrets import hash_secret

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


@cache
def dummy_password_hash() -> str:
    """A real hash of a password no account has, verified against when no account matched.

    Keeps the timing of "unknown e-mail address" close to "wrong password", so the two cannot be
    told apart by it (spec: no enumeration).

    Computed on first use and then kept: at import time the API, the reaper, both operator
    commands and every pytest process would each pay an Argon2 hash for a value only the login
    path ever looks at.
    """
    return hash_secret("no account uses this password")


def meets_password_policy(password: str) -> bool:
    """Whether ``password`` may be stored: 12 to 128 characters, no composition rules."""
    return MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
