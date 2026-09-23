from collections.abc import Iterable, Set
from enum import StrEnum
from typing import Self


class Scope(StrEnum):
    """Defines the scopes for authentication.

    Each member is declared as ``(value, description)``. The value is the wire format; the
    description feeds the seeded ``PermissionScope`` rows and the OpenAPI security scheme, so a
    scope without a description cannot be declared.
    """

    description: str

    def __new__(cls, value: str, description: str) -> Self:
        member = str.__new__(cls, value)
        member._value_ = value
        member.description = description
        return member

    BOT_READ = "bot:read", "Read bot API surface."
    BOT_WRITE = "bot:write", "Write bot API surface."
    AUTH_CLIENTS_MANAGE = "auth:clients:manage", "Manage application clients."
    AUTH_USERS_MANAGE = "auth:users:manage", "Invite, disable and reset user accounts; assign stored roles."
    AUTH_USERS_LOGIN = (
        "auth:users:login",
        "Log users in on their behalf (password / refresh_token grants, redeem, revoke). Never part of a user's "
        "scopes.",
    )
    CRM_READ = "crm:read", "Read parties, relations and subjects."
    CRM_READ_OWN = "crm:read:own", "Read parties within the caller's reach."
    CRM_WRITE = "crm:write", "Create, change and delete parties, relations and subjects."
    ACCOUNT_SELF = (
        "account:self",
        "Manage the caller's own account. Also guarantees a user token never has an empty scope.",
    )


OWN_VARIANT: dict[Scope, Scope] = {
    Scope.CRM_READ: Scope.CRM_READ_OWN,
}
"""Maps an unqualified scope to its ``:own``, reach-qualified form (ADR 0008, decision H)."""


def parse_scopes(scopes: str | Iterable[str] | None) -> frozenset[str]:
    """Turn scopes the way a caller hands them in into a set - the one normalizer of scopes.

    An OAuth2 scope string (RFC 6749, section 3.3) is split at whitespace; any other iterable,
    ``Scope`` members included, is taken value by value, stripped, empty values dropped; ``None``
    is no scope. It runs where scopes enter: the token form, a token claim, a stored scope
    column. Past it everything is a set, which is why ``expand`` and ``canonical`` take a
    ``Set`` - a ``str`` is not one, so it cannot slip in and be iterated character by character.
    """
    if scopes is None:
        return frozenset()
    if isinstance(scopes, str):
        return frozenset(scopes.split())

    return frozenset(value for scope in scopes if (value := scope.strip()))


def format_scopes(scopes: Iterable[str]) -> str:
    """The OAuth2 scope string of ``scopes``: sorted and space-separated, the inverse of ``parse_scopes``."""
    return " ".join(sorted(scopes))


def expand(scopes: Set[str]) -> frozenset[str]:
    """Return the closure of ``scopes``: every scope plus the ``:own`` variant of each unqualified
    one it contains.

    Used to check what a set of scopes *permits*: ``get_current_principal`` expands the principal's
    scopes before comparing them against a route's requirement, so a token carrying the unqualified
    scope satisfies a route that asks for the qualified one.
    """
    return frozenset(scopes) | {own for base, own in OWN_VARIANT.items() if base in scopes}


def canonical(scopes: Set[str]) -> frozenset[str]:
    """Return the canonical form of ``scopes``: drop ``x:own`` wherever the unqualified ``x`` is
    also present.

    The inverse of :func:`expand` on any already-canonical set. A token always carries the
    canonical form - it is redundant to hold both a scope and its own-qualified variant.
    """
    return frozenset(scopes) - {own for base, own in OWN_VARIANT.items() if base in scopes}
