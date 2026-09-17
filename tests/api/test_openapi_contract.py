"""Contract-level assertions over the generated OpenAPI document."""

from typing import Any

import pytest

from app.core.auth import Scope
from app.main import app


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    return app.openapi()


def test_security_scheme_lists_every_scope_with_its_description(schema: dict[str, Any]):
    flows = [scheme["flows"] for scheme in schema["components"]["securitySchemes"].values() if "flows" in scheme]

    assert flows
    for flow in flows:
        assert flow["clientCredentials"]["scopes"] == {scope.value: scope.description for scope in Scope}
