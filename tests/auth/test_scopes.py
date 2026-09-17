import pytest

from app.core.auth import Scope


@pytest.mark.parametrize("scope", list(Scope))
def test_scope_carries_a_description(scope: Scope):
    assert scope.description.strip()


def test_scope_value_stays_the_wire_format():
    assert Scope.BOT_READ.value == "bot:read"
    assert f"{Scope.BOT_READ}" == "bot:read"
    assert Scope.BOT_READ == "bot:read"


def test_scope_lookup_by_value_returns_the_member():
    assert Scope("auth:clients:manage") is Scope.AUTH_CLIENTS_MANAGE
