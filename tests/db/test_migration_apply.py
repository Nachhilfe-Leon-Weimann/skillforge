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


async def _type_exists(url: str, schema: str, type_name: str) -> bool:
    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        row = await conn.fetchrow(
            """
            SELECT 1
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE t.typname = $1 AND n.nspname = $2
            """,
            type_name,
            schema,
        )
        return row is not None
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


_OPERATION_STATUSES_WITH_CANCELLED = ["prepared", "committed", "expired", "failed", "cancelled"]
_OPERATION_STATUSES_WITHOUT_CANCELLED = _OPERATION_STATUSES_WITH_CANCELLED[:4]


def test_cancelled_operation_status_migration_is_reversible(migration_db_url: str) -> None:
    # `alembic check` is blind to enum-label drift, so assert the actual labels the migration
    # path produces: forward adds `cancelled`, downgrade recreates the type without it
    # (exercising the enum-recreate recast under a server_default), re-upgrade adds it back.
    _alembic(migration_db_url, "upgrade", "head")
    assert asyncio.run(_enum_labels(migration_db_url, "bot", "operation_status")) == _OPERATION_STATUSES_WITH_CANCELLED

    _alembic(migration_db_url, "downgrade", "0008_idempotent_prepare")
    assert (
        asyncio.run(_enum_labels(migration_db_url, "bot", "operation_status")) == _OPERATION_STATUSES_WITHOUT_CANCELLED
    )

    _alembic(migration_db_url, "upgrade", "head")
    assert asyncio.run(_enum_labels(migration_db_url, "bot", "operation_status")) == _OPERATION_STATUSES_WITH_CANCELLED


_USER_ACCOUNT_ENUM_TYPES = ["user_account_status", "user_account_role_name", "user_action_token_purpose"]

_SEED_USER_ACCOUNT_TABLES = """
INSERT INTO core.party (id, type) VALUES ('11111111-1111-1111-1111-111111111111', 'PERSON');
INSERT INTO auth.application_client (id, client_id, name)
    VALUES ('22222222-2222-2222-2222-222222222222', 'seed-client', 'Seed Client');
INSERT INTO auth.user_account (id, party_id, email, status)
    VALUES ('33333333-3333-3333-3333-333333333333',
            '11111111-1111-1111-1111-111111111111', 'seed@example.com', 'invited');
INSERT INTO auth.user_account_role (user_account_id, role)
    VALUES ('33333333-3333-3333-3333-333333333333', 'admin');
INSERT INTO auth.user_session (id, user_account_id, application_client_id, scope, refresh_token_hash, expires_at)
    VALUES ('44444444-4444-4444-4444-444444444444', '33333333-3333-3333-3333-333333333333',
            '22222222-2222-2222-2222-222222222222', 'account:self', 'some-hash', now() + interval '30 days');
INSERT INTO auth.user_action_token (id, user_account_id, purpose, token_hash, expires_at, issued_by)
    VALUES ('55555555-5555-5555-5555-555555555555', '33333333-3333-3333-3333-333333333333',
            'invitation', 'token-hash', now() + interval '7 days', 'cli');
"""


def test_user_account_tables_migration_is_reversible_and_drops_its_enum_types(migration_db_url: str) -> None:
    # Forward against an empty database, then seed every new table so the downgrade below runs
    # against data (not just an empty schema) before the re-upgrade recreates them.
    _alembic(migration_db_url, "upgrade", "head")
    assert all(asyncio.run(_type_exists(migration_db_url, "public", name)) for name in _USER_ACCOUNT_ENUM_TYPES)

    asyncio.run(_run_on_server(migration_db_url, _SEED_USER_ACCOUNT_TABLES))

    _alembic(migration_db_url, "downgrade", "0010_subject_title_unique")
    assert not any(asyncio.run(_type_exists(migration_db_url, "public", name)) for name in _USER_ACCOUNT_ENUM_TYPES)

    _alembic(migration_db_url, "upgrade", "head")
    assert all(asyncio.run(_type_exists(migration_db_url, "public", name)) for name in _USER_ACCOUNT_ENUM_TYPES)
