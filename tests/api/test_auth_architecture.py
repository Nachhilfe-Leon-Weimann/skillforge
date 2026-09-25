"""Standing criterion of the user-authentication spec: the auth core depends on no service and no API module."""

from tests.api.test_crm_architecture import REPO_ROOT, _imported_modules

AUTH_CORE = "app/core/auth"
FORBIDDEN = ("app.services", "app.api")


def _violations(source: str, *, package: str) -> set[str]:
    return {
        module
        for module in _imported_modules(source, package=package)
        if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN)
    }


def test_the_auth_core_imports_neither_services_nor_the_api():
    files = list((REPO_ROOT / AUTH_CORE).rglob("*.py"))

    assert files
    for path in files:
        package = ".".join(path.parent.relative_to(REPO_ROOT).parts)
        assert _violations(path.read_text(), package=package) == set(), path.relative_to(REPO_ROOT)


def test_the_check_catches_absolute_and_relative_imports():
    package = "app.core.auth.services"

    assert _violations("from app.services.crm import parties", package=package)
    assert _violations("import app.api.v1.common", package=package)
    assert _violations("from ....api.v1 import common", package=package)
    assert (
        _violations("from ..services import tokens\nfrom app.core.db.models import UserAccount", package=package)
        == set()
    )
