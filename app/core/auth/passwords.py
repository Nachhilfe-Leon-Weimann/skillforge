"""The password policy - its one home (user-authentication spec, decision B).

Checked in the services rather than in a request schema, so that every route that sets a password
shares one rule and one ``weak_password`` code. Hashing lives in ``secrets.py``: a password is hashed
like a client secret.
"""

from functools import cache

from .secrets import hash_secret, off_the_loop, verify_secret

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def meets_password_policy(password: str) -> bool:
    """Whether ``password`` may be stored: 8 to 128 characters, nothing else.

    It has to be text: a string with a lone surrogate (valid JSON, not valid UTF-8) cannot be hashed.
    """
    return MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH and _is_text(password)


def _is_text(value: str) -> bool:
    try:
        value.encode()
    except UnicodeEncodeError:
        return False

    return True


@cache
def dummy_password_hash() -> str:
    """Return a real hash of a password no account has, to verify against when no account matched.

    Keeps "unknown account" as slow as "wrong password" (no enumeration). Computed on first use and
    then kept: only the login path needs it, and an Argon2 hash at import time would cost every
    process that imports the auth core.
    """
    return hash_secret("no account uses this password")


async def verify_dummy_password(password: str) -> None:
    """Verify ``password`` against ``dummy_password_hash()`` and discard the answer, off the event loop.

    What a login does when no account can be checked, so that it takes as long as a wrong password. The
    first call also computes the dummy hash - in the worker thread too.
    """
    await off_the_loop(_verify_dummy, password)


def _verify_dummy(password: str) -> None:
    verify_secret(password, dummy_password_hash())
