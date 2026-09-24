from collections.abc import Iterable
from enum import StrEnum

from .scopes import Scope


class Role(StrEnum):
    """A view a user account holds; an account can hold several.

    `student`, `tutor` and `guardian` follow from the CRM, `admin` is assigned to the account.
    """

    # The docstring above is the contract's description of the enum. Behind it: the derived roles
    # are computed at every token issuance and refresh and never stored; ``ADMIN`` is a row in
    # ``auth.user_account_role`` (ADR 0008). SkillForge authorizes by scope only and never branches
    # on a role; a client narrows a token to one view by requesting fewer scopes.

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

The derived roles carry no scopes of their own yet - they tell ``/auth/me`` and the token's
``roles`` claim which views to offer. ``admin`` starts without ``bot:write`` (an open question of
the user authentication spec); adding it is one line here.
"""


def scopes_for(roles: Iterable[Role]) -> frozenset[Scope]:
    """Return the scopes a user account with ``roles`` holds: ``BASE_USER_SCOPES`` plus every role's own."""
    return BASE_USER_SCOPES.union(*(ROLE_SCOPES[role] for role in roles))
