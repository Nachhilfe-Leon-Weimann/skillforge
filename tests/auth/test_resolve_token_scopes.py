import pytest

from app.core.auth.services.errors import InvalidClientScopeError
from app.core.auth.services.scopes import resolve_token_scopes


def test_client_credentials_with_no_scope_requested_gets_the_canonical_ceiling():
    """``user_scopes`` omitted: the ``client_credentials`` computation, unchanged apart from
    widening (a client granted ``crm:read`` also carries its ``:own`` variant in the ceiling, but
    canonical drops it again since the unqualified scope is present)."""
    scopes = resolve_token_scopes(
        requested_scopes=None,
        granted_scopes=frozenset({"bot:read", "crm:read"}),
    )

    assert scopes == {"bot:read", "crm:read"}


def test_client_credentials_may_request_the_own_variant_of_a_granted_scope():
    scopes = resolve_token_scopes(
        requested_scopes=["crm:read:own"],
        granted_scopes=frozenset({"crm:read"}),
    )

    assert scopes == {"crm:read:own"}


def test_client_credentials_rejects_a_scope_without_a_grant():
    with pytest.raises(InvalidClientScopeError):
        resolve_token_scopes(
            requested_scopes=["crm:write"],
            granted_scopes=frozenset({"crm:read"}),
        )


def test_client_credentials_with_no_grants_and_no_request_is_invalid_scope():
    with pytest.raises(InvalidClientScopeError, match="Client has no active scope grants"):
        resolve_token_scopes(
            requested_scopes=None,
            granted_scopes=frozenset(),
        )


def test_user_grant_intersects_client_grants_with_user_scopes_when_none_requested():
    scopes = resolve_token_scopes(
        requested_scopes=None,
        granted_scopes=frozenset({"account:self", "crm:read", "crm:write"}),
        user_scopes=frozenset({"account:self", "crm:read:own"}),
    )

    assert scopes == {"account:self", "crm:read:own"}


def test_user_grant_the_client_is_the_ceiling_for_account_self_too():
    scopes = resolve_token_scopes(
        requested_scopes=None,
        granted_scopes=frozenset({"crm:read", "crm:write"}),
        user_scopes=frozenset({"account:self", "crm:read:own"}),
    )

    assert scopes == {"crm:read:own"}


def test_user_grant_with_a_disjoint_client_grant_is_invalid_scope():
    """The client does hold grants - just none that overlap the user's scopes - so the error must
    not blame the client for having no grants at all (that message points an operator at the wrong
    cause, and it ends up in the ``token.denied`` audit detail)."""
    with pytest.raises(InvalidClientScopeError, match="Client grants and user scopes have no scope in common"):
        resolve_token_scopes(
            requested_scopes=None,
            granted_scopes=frozenset({"bot:read"}),
            user_scopes=frozenset({"account:self", "crm:read:own"}),
        )


def test_user_grant_with_no_client_grants_at_all_still_blames_the_client():
    with pytest.raises(InvalidClientScopeError, match="Client has no active scope grants"):
        resolve_token_scopes(
            requested_scopes=None,
            granted_scopes=frozenset(),
            user_scopes=frozenset({"account:self", "crm:read:own"}),
        )


def test_user_grant_requesting_more_than_the_ceiling_is_invalid_scope():
    with pytest.raises(InvalidClientScopeError):
        resolve_token_scopes(
            requested_scopes=["crm:write"],
            granted_scopes=frozenset({"account:self", "crm:read", "crm:write"}),
            user_scopes=frozenset({"account:self", "crm:read:own"}),
        )


def test_user_grant_requesting_a_narrower_scope_than_the_ceiling_is_allowed():
    scopes = resolve_token_scopes(
        requested_scopes="account:self",
        granted_scopes=frozenset({"account:self", "crm:read", "crm:write"}),
        user_scopes=frozenset({"account:self", "crm:read:own"}),
    )

    assert scopes == {"account:self"}
