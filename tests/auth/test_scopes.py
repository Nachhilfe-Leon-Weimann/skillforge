from itertools import combinations

import pytest

from app.core.auth import Scope
from app.core.auth.scopes import OWN_VARIANT, canonical, expand, format_scopes, parse_scopes


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


def test_parse_scopes_splits_an_oauth2_scope_string_at_whitespace():
    assert parse_scopes(" bot:read\tcrm:read  ") == {"bot:read", "crm:read"}


def test_parse_scopes_takes_an_iterable_value_by_value_and_drops_empty_values():
    """A ``str`` is split, anything else iterated - so a single scope string is never taken apart
    character by character."""
    assert parse_scopes([Scope.BOT_READ, " crm:read ", "", "  "]) == {"bot:read", "crm:read"}


def test_parse_scopes_reads_none_as_no_scope():
    assert parse_scopes(None) == frozenset()


def test_format_scopes_is_the_sorted_inverse_of_parse_scopes():
    assert format_scopes({"crm:read", "bot:read"}) == "bot:read crm:read"
    assert parse_scopes(format_scopes({"crm:read", "bot:read"})) == {"bot:read", "crm:read"}


def test_canonical_is_the_inverse_of_expand_on_every_subset_of_scope():
    """Property-style check over every subset of ``Scope``:

    (a) ``expand`` only cares about the canonical form of its input - expanding a subset gives the
        same result as expanding its canonical form.
    (b) ``canonical`` undoes ``expand``: taking the canonical form of an expanded subset gives back
        the canonical form of the original subset.
    (c) ``canonical`` is a genuine inverse of ``expand`` on an already-canonical subset: expanding
        and then canonicalizing it returns exactly that subset, unchanged.
    """
    members = list(Scope)
    for size in range(len(members) + 1):
        for combo in combinations(members, size):
            subset = frozenset(combo)
            expanded = expand(subset)
            canonicalized = canonical(subset)

            assert expand(canonicalized) == expanded
            assert canonical(expanded) == canonicalized
            if canonicalized == subset:
                assert canonical(expand(subset)) == subset
