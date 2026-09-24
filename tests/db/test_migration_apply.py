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


async def _rows(url: str, query: str, *args: object) -> list[tuple[object, ...]]:
    conn = await asyncpg.connect(_asyncpg_dsn(url))
    try:
        return [tuple(row) for row in await conn.fetch(query, *args)]
    finally:
        await conn.close()


async def _primary_key_columns(url: str, schema: str, table: str) -> list[str]:
    rows = await _rows(
        url,
        """
        SELECT a.attname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, position)
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.contype = 'p' AND t.relname = $1 AND n.nspname = $2
        ORDER BY k.position
        """,
        table,
        schema,
    )
    return [str(name) for (name,) in rows]


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


_REVISION_0010 = "0010_subject_title_unique"
_REVISION_0011 = "0011_grant_mode_user_accounts"

_REVISION_0011_ENUM_LABELS = {
    "grant_mode": ["application", "delegated"],
    "user_account_status": ["active", "disabled"],
    "user_account_role_name": ["admin"],
    "user_action_token_purpose": ["invitation", "password_reset"],
}
_GRANT_TABLE = ("auth", "application_client_scope_grant")
_GRANT_KEY_WITH_MODE = ["application_client_id", "scope_key", "mode"]
_GRANT_KEY_WITHOUT_MODE = _GRANT_KEY_WITH_MODE[:2]

_PARTY_ID = "11111111-1111-1111-1111-111111111111"
_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
_ACCOUNT_ID = "33333333-3333-3333-3333-333333333333"
_OTHER_PARTY_ID = "44444444-4444-4444-4444-444444444444"

# What the revision meets in production: a client holding a grant that has no mode yet.
_SEED_BEFORE_GRANT_MODES = f"""
INSERT INTO auth.permission_scope (key, description) VALUES ('crm:read', 'Read the CRM');
INSERT INTO core.party (id, type) VALUES ('{_PARTY_ID}', 'PERSON');
INSERT INTO auth.application_client (id, client_id, name) VALUES ('{_CLIENT_ID}', 'seed-client', 'Seed Client');
INSERT INTO auth.application_client_scope_grant (application_client_id, scope_key) VALUES ('{_CLIENT_ID}', 'crm:read');
"""

# A row in every new table - the account's status left to its server default - and a `delegated`
# grant of the scope the client already holds as an `application` grant.
_SEED_ACCOUNT_AND_DELEGATED_GRANT = f"""
INSERT INTO auth.user_account (id, party_id, email) VALUES ('{_ACCOUNT_ID}', '{_PARTY_ID}', 'admin@example.org');
INSERT INTO auth.user_account_role (user_account_id, role) VALUES ('{_ACCOUNT_ID}', 'admin');
INSERT INTO auth.user_session (id, user_account_id, application_client_id, scope, refresh_token_hash, expires_at)
    VALUES (gen_random_uuid(), '{_ACCOUNT_ID}', '{_CLIENT_ID}', 'account:self', 'refresh-hash',
            now() + interval '30 days');
INSERT INTO auth.user_action_token (id, user_account_id, purpose, token_hash, expires_at, issued_by)
    VALUES (gen_random_uuid(), '{_ACCOUNT_ID}', 'invitation', 'action-hash', now() + interval '7 days', 'cli');
INSERT INTO auth.application_client_scope_grant (application_client_id, scope_key, mode)
    VALUES ('{_CLIENT_ID}', 'crm:read', 'delegated');
"""

# Another person whose login e-mail is not lowercased. Both statements run in one implicit
# transaction, so the refused account takes its party with it.
_SEED_UPPERCASE_EMAIL = f"""
INSERT INTO core.party (id, type) VALUES ('{_OTHER_PARTY_ID}', 'PERSON');
INSERT INTO auth.user_account (id, party_id, email) VALUES (gen_random_uuid(), '{_OTHER_PARTY_ID}', 'Anna@Example.org');
"""


def test_grant_mode_and_user_account_migration_is_reversible(migration_db_url: str) -> None:
    # Autogenerate neither creates the type of an added enum column nor detects a primary-key
    # change, and `alembic check` is blind to both and to check constraints - so assert the types,
    # the key, the e-mail check and the rows on a database that holds data on either side of the
    # revision. The empty-database path is covered by test_migrations_apply_match_models_and_reverse.
    _alembic(migration_db_url, "upgrade", _REVISION_0010)
    asyncio.run(_run_on_server(migration_db_url, _SEED_BEFORE_GRANT_MODES))

    _alembic(migration_db_url, "upgrade", _REVISION_0011)
    for name, labels in _REVISION_0011_ENUM_LABELS.items():
        assert asyncio.run(_enum_labels(migration_db_url, "public", name)) == labels
    assert asyncio.run(_primary_key_columns(migration_db_url, *_GRANT_TABLE)) == _GRANT_KEY_WITH_MODE
    grants = "SELECT application_client_id::text, scope_key, mode::text FROM auth.application_client_scope_grant"
    assert asyncio.run(_rows(migration_db_url, grants)) == [(_CLIENT_ID, "crm:read", "application")]

    asyncio.run(_run_on_server(migration_db_url, _SEED_ACCOUNT_AND_DELEGATED_GRANT))
    assert asyncio.run(_rows(migration_db_url, "SELECT status::text FROM auth.user_account")) == [("active",)]
    with pytest.raises(asyncpg.exceptions.CheckViolationError) as refused:
        asyncio.run(_run_on_server(migration_db_url, _SEED_UPPERCASE_EMAIL))
    assert getattr(refused.value, "constraint_name", None) == "ck_user_account_email_lowercase"

    _alembic(migration_db_url, "downgrade", _REVISION_0010)
    for name in _REVISION_0011_ENUM_LABELS:
        assert asyncio.run(_enum_labels(migration_db_url, "public", name)) == [], f"{name} survived the downgrade"
    assert asyncio.run(_primary_key_columns(migration_db_url, *_GRANT_TABLE)) == _GRANT_KEY_WITHOUT_MODE
    # The party, the client and its original grant survive; the delegated grant does not.
    assert asyncio.run(_rows(migration_db_url, "SELECT id::text FROM core.party")) == [(_PARTY_ID,)]
    assert asyncio.run(_rows(migration_db_url, "SELECT id::text FROM auth.application_client")) == [(_CLIENT_ID,)]
    grants = "SELECT application_client_id::text, scope_key FROM auth.application_client_scope_grant"
    assert asyncio.run(_rows(migration_db_url, grants)) == [(_CLIENT_ID, "crm:read")]

    _alembic(migration_db_url, "upgrade", _REVISION_0011)
    assert asyncio.run(_primary_key_columns(migration_db_url, *_GRANT_TABLE)) == _GRANT_KEY_WITH_MODE
