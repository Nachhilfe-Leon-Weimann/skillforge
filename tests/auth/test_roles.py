import pytest

from app.core.auth import Scope
from app.core.auth.roles import BASE_USER_SCOPES, ROLE_SCOPES, STORED_ROLES, Role, scopes_for


def test_stored_roles_is_exactly_admin():
    assert STORED_ROLES == {Role.ADMIN}


def test_base_user_scopes_is_account_self_and_crm_read_own():
    assert BASE_USER_SCOPES == {Scope.ACCOUNT_SELF, Scope.CRM_READ_OWN}


@pytest.mark.parametrize("role", [Role.STUDENT, Role.TUTOR, Role.GUARDIAN])
def test_derived_roles_carry_no_scopes_of_their_own(role: Role):
    assert ROLE_SCOPES[role] == frozenset()


def test_role_scopes_has_an_entry_for_every_role():
    """``scopes_for`` indexes ``ROLE_SCOPES[role]`` without a default, so a ``Role`` member added
    later without a mapping entry would be a bare ``KeyError`` at token issuance."""
    assert set(ROLE_SCOPES) == set(Role)


def test_admin_role_scopes_match_the_spec_and_exclude_bot_write():
    assert ROLE_SCOPES[Role.ADMIN] == {
        Scope.CRM_READ,
        Scope.CRM_WRITE,
        Scope.AUTH_USERS_MANAGE,
        Scope.AUTH_CLIENTS_MANAGE,
        Scope.BOT_READ,
    }
    assert Scope.BOT_WRITE not in ROLE_SCOPES[Role.ADMIN]


def test_scopes_for_no_roles_returns_the_base_user_scopes():
    assert scopes_for([]) == BASE_USER_SCOPES


def test_scopes_for_admin_adds_the_admin_scopes_to_the_base():
    assert scopes_for([Role.ADMIN]) == BASE_USER_SCOPES | ROLE_SCOPES[Role.ADMIN]


def test_scopes_for_a_tutor_who_is_also_an_admin_holds_both_roles_scopes():
    assert scopes_for([Role.TUTOR, Role.ADMIN]) == BASE_USER_SCOPES | ROLE_SCOPES[Role.ADMIN]


def test_no_role_carries_client_only_scopes():
    """``auth:users:login`` is a client-only scope: it must never reach a user token, so it may not
    appear in the base scopes every user gets nor in any role's scopes."""
    all_role_scopes: frozenset[Scope] = frozenset().union(*ROLE_SCOPES.values())

    assert Scope.AUTH_USERS_LOGIN not in BASE_USER_SCOPES
    assert Scope.AUTH_USERS_LOGIN not in all_role_scopes
