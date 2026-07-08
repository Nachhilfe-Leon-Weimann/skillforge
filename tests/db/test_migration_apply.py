"""Run the real Alembic migrations against a throwaway database.

The other DB tests build their schema from the models via ``Base.metadata.create_all``
and therefore never exercise the migrations. This test closes that gap: it applies the
migrations on an empty database, asserts the result matches the models (no drift), and
verifies the chain is reversible - catching breakage in CI instead of on a prod deploy.

Alembic runs in a subprocess on purpose: in-process it would run ``logging.fileConfig``
(disabling the app loggers other tests assert on) and resolve ``Base.metadata`` from this
process (where test-only models such as tests/db/test_base.py's would leak into ``check``).
A subprocess keeps that global state isolated and matches how the migrate service runs.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.db


def _asyncpg_dsn(url: str) -> str:
    # SQLAlchemy URL (postgresql+asyncpg://...) -> plain libpq DSN for asyncpg.
    return url.replace("+asyncpg", "")


def _with_database(url: str, database: str) -> str:
    return urlunparse(urlparse(url)._replace(path=f"/{database}"))


async def _run_on_server(server_url: str, statement: str) -> None:
    conn = await asyncpg.connect(_asyncpg_dsn(server_url))
    try:
        await conn.execute(statement)
    finally:
        await conn.close()


async def _enum_labels(url: str, schema: str, enum_name: str) -> list[str]:
    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        rows = await conn.fetch(
            """
            SELECT e.enumlabel
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE t.typname = $1 AND n.nspname = $2
            ORDER BY e.enumsortorder
            """,
            enum_name,
            schema,
        )
        return [row["enumlabel"] for row in rows]
    finally:
        await conn.close()


@pytest.fixture
def migration_db_url(db_url: str):
    """A freshly created, empty database so migrations run from a clean slate."""
    name = f"migtest_{uuid.uuid4().hex[:12]}"
    asyncio.run(_run_on_server(db_url, f'CREATE DATABASE "{name}"'))
    try:
        yield _with_database(db_url, name)
    finally:
        asyncio.run(_run_on_server(db_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _alembic(db_url: str, *args: str) -> None:
    env = {**os.environ, "DB__URL": db_url, "DB__MIGRATION_URL": db_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"`alembic {' '.join(args)}` failed:\n{result.stdout}\n{result.stderr}"


def test_migrations_apply_match_models_and_reverse(migration_db_url: str) -> None:
    # 1. Every migration applies cleanly from an empty database.
    _alembic(migration_db_url, "upgrade", "head")
    # 2. The migrated schema matches the models - fails if a revision is missing or
    #    incomplete (drift), which create_all-based tests cannot detect.
    _alembic(migration_db_url, "check")
    # 3. The chain is reversible and can be rebuilt from scratch.
    _alembic(migration_db_url, "downgrade", "base")
    _alembic(migration_db_url, "upgrade", "head")


_OPERATION_KINDS_WITH_OFF_BOARDING = [
    "tutor_activate",
    "student_activate",
    "student_stash",
    "student_pop",
    "student_deactivate",
    "tutor_deactivate",
]
_OPERATION_KINDS_WITHOUT_OFF_BOARDING = _OPERATION_KINDS_WITH_OFF_BOARDING[:4]


def test_off_boarding_operation_kinds_migration_is_reversible(migration_db_url: str) -> None:
    # `alembic check` is blind to enum-label drift, so assert the actual DB labels the
    # migration path produces - forward adds the off-boarding kinds, downgrade removes them
    # (exercising the enum-recreate recast), re-upgrade adds them back.
    _alembic(migration_db_url, "upgrade", "head")
    assert asyncio.run(_enum_labels(migration_db_url, "bot", "operation_kind")) == _OPERATION_KINDS_WITH_OFF_BOARDING

    _alembic(migration_db_url, "downgrade", "0006_worker_heartbeat")
    assert asyncio.run(_enum_labels(migration_db_url, "bot", "operation_kind")) == _OPERATION_KINDS_WITHOUT_OFF_BOARDING

    _alembic(migration_db_url, "upgrade", "head")
    assert asyncio.run(_enum_labels(migration_db_url, "bot", "operation_kind")) == _OPERATION_KINDS_WITH_OFF_BOARDING
