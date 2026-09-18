"""ADR 0007: the CRM is the system of record and never depends on the bot domain."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRM_PACKAGES = ("app/services/crm", "app/api/v1/crm")
FORBIDDEN = ("app.services.bot", "app.api.v1.bot")


def _imported_modules(source: str, *, package: str) -> set[str]:
    """Return the absolute module names a source file imports, resolving relative imports."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package.split(".")
            prefix = ".".join(base[: len(base) - (node.level - 1)]) if node.level else ""
            module = ".".join(part for part in (prefix, node.module or "") if part)
            modules.add(module)
            modules.update(f"{module}.{alias.name}" for alias in node.names)
    return modules


def _violations(source: str, *, package: str) -> set[str]:
    return {
        module
        for module in _imported_modules(source, package=package)
        if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN)
    }


def test_the_crm_never_imports_the_bot_domain():
    files = [path for package in CRM_PACKAGES for path in (REPO_ROOT / package).rglob("*.py")]

    assert {path.parent.relative_to(REPO_ROOT).as_posix() for path in files} >= set(CRM_PACKAGES)
    for path in files:
        package = ".".join(path.parent.relative_to(REPO_ROOT).parts)
        assert _violations(path.read_text(), package=package) == set(), path.relative_to(REPO_ROOT)


def test_the_import_check_catches_every_import_form():
    package = "app.services.crm"

    assert _violations("import app.services.bot.profile", package=package)
    assert _violations("from app.services.bot import load_party_for_discord_id", package=package)
    assert _violations("from app.services import bot", package=package)
    assert _violations("from app.api.v1.bot.schemas import JobListItem", package=package)
    assert _violations("from .. import bot", package=package)
    assert _violations("from ..bot.errors import BotServiceError", package=package)
    assert _violations("from . import subjects\nfrom app.core.errors import NotFoundError", package=package) == set()
