from collections.abc import Iterable
from enum import StrEnum

from .scopes import Scope


class Role(StrEnum):
    """A view a user account holds; an account can hold several.

    `student`, `tutor` and `guardian` follow from the CRM, `admin` is assigned to the account.
    """

    # The docstring above is the contract's description of the enum. Behind it: the derived roles
    # are computed at every token issuance and refresh and never stored; ``ADMIN`` is a row in
    # ``auth.user_account_role`` (ADR 0008, decision J). A client narrows a token to one view by
    # requesting fewer scopes.

    STUDENT = "student"
    TUTOR = "tutor"
    GUARDIAN = "guardian"
    ADMIN = "admin"


STORED_ROLES: frozenset[Role] = frozenset({Role.ADMIN})
"""Roles that are rows in ``auth.user_account_role`` rather than derived from the CRM."""

BASE_USER_SCOPES: frozenset[Scope] = frozenset({Scope.ACCOUNT_SELF, Scope.CRM_READ_OWN})
"""Scopes every user account holds, regardless of its roles."""

ROLE_SCOPES: dict[Role, frozenset[Scope]] = {
    Role.STUDENT: frozenset(),
    Role.TUTOR: frozenset(),
    Role.GUARDIAN: frozenset(),
    Role.ADMIN: frozenset({
        Scope.CRM_READ,
        Scope.CRM_WRITE,
        Scope.AUTH_USERS_MANAGE,
        Scope.AUTH_CLIENTS_MANAGE,
        Scope.BOT_READ,
    }),
}
"""Scopes a role adds on top of ``BASE_USER_SCOPES``.

The derived roles carry no scopes of their own yet - they exist so ``/auth/me`` and the token's
``roles`` claim tell the portal which views to offer. ``admin`` starts without ``bot:write`` (open
question in ADR 0008): a human cannot yet drive two-phase bot operations past the bot.
"""


def scopes_for(roles: Iterable[Role]) -> frozenset[Scope]:
    """Return the scopes a user account with ``roles`` holds: ``BASE_USER_SCOPES`` plus every
    role's own scopes."""
    scopes = set(BASE_USER_SCOPES)
    for role in roles:
        scopes |= ROLE_SCOPES[role]

    return frozenset(scopes)
