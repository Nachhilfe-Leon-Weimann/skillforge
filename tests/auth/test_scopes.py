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


def test_expand_treats_a_bare_string_as_a_space_separated_scope_string():
    """A ``str`` type-checks as ``Iterable[str]``, so without special-casing it, ``expand`` would
    iterate it character by character - the same pitfall ``normalize_scope_set`` in
    ``services/scopes.py`` guards against."""
    assert expand("crm:read") == {"crm:read", "crm:read:own"}


def test_expand_splits_a_multi_scope_string_on_whitespace():
    assert expand("bot:read crm:read") == {"bot:read", "crm:read", "crm:read:own"}


def test_canonical_treats_a_bare_string_as_a_space_separated_scope_string():
    assert canonical("crm:read crm:read:own") == {"crm:read"}


def test_canonical_strips_the_values_of_an_iterable_before_comparing_them():
    """A padded value has to normalize like the bare one, or the `:own` variant survives next to a
    scope that is present after all - and `_format_scope` strips it into the token anyway."""
    assert canonical(["crm:read ", "crm:read:own"]) == {"crm:read"}


def test_expand_strips_the_values_of_an_iterable_before_comparing_them():
    assert expand([" crm:read"]) == {"crm:read", "crm:read:own"}


def test_expand_drops_empty_values_of_an_iterable():
    assert expand(["bot:read", "", "  "]) == {"bot:read"}


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
