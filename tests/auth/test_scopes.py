from itertools import combinations

import pytest

from app.core.auth import Scope
from app.core.auth.scopes import OWN_VARIANT, canonical, expand


@pytest.mark.parametrize("scope", list(Scope))
def test_scope_carries_a_description(scope: Scope):
    assert scope.description.strip()


def test_scope_value_stays_the_wire_format():
    assert Scope.BOT_READ.value == "bot:read"
    assert f"{Scope.BOT_READ}" == "bot:read"
    assert Scope.BOT_READ == "bot:read"


def test_scope_lookup_by_value_returns_the_member():
    assert Scope("auth:clients:manage") is Scope.AUTH_CLIENTS_MANAGE


def test_own_variant_has_exactly_the_one_p0_entry():
    assert OWN_VARIANT == {Scope.CRM_READ: Scope.CRM_READ_OWN}


def test_expand_adds_the_own_variant_of_an_unqualified_scope():
    assert expand({Scope.CRM_READ}) == {"crm:read", "crm:read:own"}


def test_expand_leaves_a_scope_without_an_own_variant_unchanged():
    assert expand({Scope.BOT_READ}) == {"bot:read"}


def test_expand_leaves_an_already_qualified_scope_unchanged():
    assert expand({Scope.CRM_READ_OWN}) == {"crm:read:own"}


def test_canonical_drops_the_own_variant_when_the_unqualified_scope_is_present():
    assert canonical({Scope.CRM_READ, Scope.CRM_READ_OWN}) == {"crm:read"}


def test_canonical_keeps_the_own_variant_when_the_unqualified_scope_is_absent():
    assert canonical({Scope.CRM_READ_OWN}) == {"crm:read:own"}


def test_canonical_is_the_inverse_of_expand_on_every_subset_of_scope():
    """Property-style check: for every subset of ``Scope``, expanding it and then taking the
    canonical form yields the same result as taking the canonical form directly - expand never
    changes the canonical form of what it started from."""
    members = list(Scope)
    for size in range(len(members) + 1):
        for combo in combinations(members, size):
            subset = frozenset(combo)
            assert canonical(expand(subset)) == canonical(subset)
