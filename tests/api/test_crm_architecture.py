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


# --- standing criteria of the CRM API spec: what an endpoint module must not contain ---

API_PACKAGE = REPO_ROOT / "app/api/v1/crm"
ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete"}


def _endpoints(tree: ast.AST) -> list[ast.AsyncFunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr in ROUTE_DECORATORS
            for decorator in node.decorator_list
        )
    ]


def _boilerplate(source: str) -> list[str]:
    tree = ast.parse(source)
    found = [f"try/except at line {node.lineno}" for node in ast.walk(tree) if isinstance(node, (ast.Try, ast.TryStar))]
    found += [
        f"HTTPException at line {node.lineno}"
        for node in ast.walk(tree)
        if (isinstance(node, ast.Name) and node.id == "HTTPException")
        or (isinstance(node, ast.alias) and node.name == "HTTPException")
    ]
    for endpoint in _endpoints(tree):
        arguments = [*endpoint.args.posonlyargs, *endpoint.args.args, *endpoint.args.kwonlyargs]
        for argument in arguments:
            if argument.arg == "_":
                found.append(f"{endpoint.name}: parameter named _")
            annotation = argument.annotation
            if annotation is None or any(
                isinstance(node, ast.Name) and node.id == "Annotated" for node in ast.walk(annotation)
            ):
                found.append(f"{endpoint.name}: parameter {argument.arg} is not typed by an alias")
    return found


def test_the_crm_endpoints_carry_no_boilerplate():
    modules = sorted(API_PACKAGE.glob("*.py"))
    endpoints = [endpoint.name for module in modules for endpoint in _endpoints(ast.parse(module.read_text()))]

    assert len(endpoints) == 21, endpoints
    for module in modules:
        assert _boilerplate(module.read_text()) == [], module.name


def test_the_boilerplate_check_catches_each_kind():
    offending = """
from typing import Annotated
from fastapi import APIRouter, HTTPException, Path
router = APIRouter()

@router.get("/{party_id}")
async def get_party(party_id: Annotated[int, Path()], _: object, session):
    try:
        return 1
    except ValueError:
        raise HTTPException(404)
"""

    assert _boilerplate(offending) == [
        "try/except at line 8",
        "HTTPException at line 3",
        "HTTPException at line 11",
        "get_party: parameter party_id is not typed by an alias",
        "get_party: parameter named _",
        "get_party: parameter session is not typed by an alias",
    ]
    assert _boilerplate("async def helper(x: int): ...") == []
