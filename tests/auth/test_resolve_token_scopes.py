import pytest

from app.core.auth.scopes import format_scopes
from app.core.auth.services.errors import InvalidClientScopeError
from app.core.auth.services.scopes import resolve_token_scopes

ROLE_SCOPES_OF_A_STUDENT = frozenset({"account:self", "crm:read:own"})


def test_no_scope_requested_gets_every_granted_scope_in_canonical_form():
    assert resolve_token_scopes(requested=frozenset(), granted={"bot:read", "crm:read"}) == {"bot:read", "crm:read"}


def test_a_client_granted_crm_read_may_request_crm_read_own():
    assert resolve_token_scopes(requested={"crm:read:own"}, granted={"crm:read"}) == {"crm:read:own"}


def test_requesting_an_ungranted_scope_is_invalid_scope():
    with pytest.raises(InvalidClientScopeError, match="Requested scopes are not granted"):
        resolve_token_scopes(requested={"crm:write"}, granted={"crm:read"})


def test_no_grants_and_no_request_is_invalid_scope():
    with pytest.raises(InvalidClientScopeError, match="Client has no active scope grants"):
        resolve_token_scopes(requested=frozenset(), granted=frozenset())


@pytest.mark.parametrize(
    ("granted", "token_scope"),
    [
        ({"account:self", "crm:read", "crm:write"}, "account:self crm:read:own"),
        ({"crm:read", "crm:write"}, "crm:read:own"),
    ],
)
def test_a_ceiling_narrows_the_granted_scopes(granted: set[str], token_scope: str):
    scopes = resolve_token_scopes(requested=frozenset(), granted=granted, ceilings=[ROLE_SCOPES_OF_A_STUDENT])

    assert format_scopes(scopes) == token_scope


def test_grants_outside_the_ceiling_are_invalid_scope():
    """The client holds grants, only none within the ceiling - the audit detail must not claim it has none."""
    with pytest.raises(InvalidClientScopeError, match="Client grants and ceilings have no scope in common"):
        resolve_token_scopes(requested=frozenset(), granted={"bot:read"}, ceilings=[ROLE_SCOPES_OF_A_STUDENT])


def test_every_ceiling_narrows_further():
    scopes = resolve_token_scopes(
        requested=frozenset(),
        granted={"account:self", "crm:read", "crm:write"},
        ceilings=[{"account:self", "crm:read", "crm:write"}, {"account:self"}],
    )

    assert scopes == {"account:self"}


def test_a_request_within_the_ceiling_narrows_the_token():
    scopes = resolve_token_scopes(
        requested={"account:self"},
        granted={"account:self", "crm:read", "crm:write"},
        ceilings=[ROLE_SCOPES_OF_A_STUDENT],
    )

    assert scopes == {"account:self"}


def test_a_request_beyond_the_ceiling_is_invalid_scope():
    with pytest.raises(InvalidClientScopeError):
        resolve_token_scopes(
            requested={"crm:read"},
            granted={"account:self", "crm:read", "crm:write"},
            ceilings=[ROLE_SCOPES_OF_A_STUDENT],
        )


def test_a_request_of_both_forms_is_carried_in_canonical_form():
    assert resolve_token_scopes(requested={"crm:read", "crm:read:own"}, granted={"crm:read"}) == {"crm:read"}
