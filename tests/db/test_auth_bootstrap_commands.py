"""`just bootstrap-skillbot` and `just bootstrap-client` against the test database."""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthSettings, bootstrap, issue_client_token
from app.core.db.models import (
    ApplicationClient,
    ApplicationClientScopeGrant,
    ApplicationClientSecret,
    ApplicationClientStatus,
    AuthAuditLog,
    GrantMode,
)

NEW_SECRET = re.compile(r"client_secret=(?P<plaintext>sf_live_\S+)")
RETAINED_SECRET = "client_secret=<existing usable secret retained>"

OPERATOR_APPLICATION = frozenset({"auth:users:login", "crm:write"})
OPERATOR_DELEGATED = frozenset({"account:self", "crm:read", "crm:write"})


@pytest.fixture(autouse=True)
def command_session(session: AsyncSession, monkeypatch) -> None:
    """Run the commands on the test's ``session``, in a SAVEPOINT an error rolls back - as their own transaction."""

    @asynccontextmanager
    async def _session() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    monkeypatch.setattr(bootstrap, "_session", _session)


@pytest.mark.db
async def test_bootstrap_skillbot_prints_what_it_printed_before(session: AsyncSession, capsys):
    await bootstrap.bootstrap_skillbot()
    first = capsys.readouterr().out.splitlines()
    await bootstrap.bootstrap_skillbot()
    second = capsys.readouterr().out.splitlines()

    assert first[:2] == second[:2] == ["client_id=skillbot", "scopes=bot:read bot:write"]
    assert second[2:] == [RETAINED_SECRET]
    [secret_line] = first[2:]
    assert await _token_scope(session, "skillbot", secret_line) == "bot:read bot:write"
    assert await _grants(session) == {("bot:read", GrantMode.APPLICATION), ("bot:write", GrantMode.APPLICATION)}


@pytest.mark.db
async def test_bootstrap_client_grants_in_both_modes_and_prints_a_new_secret_once(session: AsyncSession, capsys):
    await _bootstrap_operator()
    first = capsys.readouterr().out.splitlines()
    await _bootstrap_operator()
    second = capsys.readouterr().out.splitlines()

    report = [
        "client_id=operator",
        "application_scopes=auth:users:login crm:write",
        "delegated_scopes=account:self crm:read crm:write",
    ]
    assert first[:3] == second[:3] == report
    assert second[3:] == [RETAINED_SECRET]
    [secret_line] = first[3:]

    clients = (await session.execute(select(ApplicationClient))).scalars().all()
    assert [(client.client_id, client.name, client.status) for client in clients] == [
        ("operator", "operator", ApplicationClientStatus.ACTIVE)
    ]
    assert len((await session.execute(select(ApplicationClientSecret))).scalars().all()) == 1
    assert await _grants(session) == {
        *((scope, GrantMode.APPLICATION) for scope in OPERATOR_APPLICATION),
        *((scope, GrantMode.DELEGATED) for scope in OPERATOR_DELEGATED),
    }
    # The rerun changed nothing, so it recorded nothing.
    assert await _event_types(session) == [
        "application_client.created",
        "scope_grant.added",
        "scope_grant.added",
        "client_secret.created",
        "scope_grant.added",
        "scope_grant.added",
        "scope_grant.added",
    ]
    # The printed secret is the client's; its own token draws on the application grants only.
    assert await _token_scope(session, "operator", secret_line) == "auth:users:login crm:write"


@pytest.mark.db
async def test_bootstrap_client_refuses_a_client_only_scope_as_delegated_and_changes_nothing(
    session: AsyncSession, capsys
):
    with pytest.raises(SystemExit) as exit_code:
        await bootstrap.bootstrap_client(
            "portal", application=frozenset({"auth:users:login"}), delegated=frozenset({"auth:users:login"})
        )

    assert exit_code.value.code == "invalid_scope: Client-only scopes cannot be granted in delegated mode"
    assert capsys.readouterr().out == ""
    assert (await session.execute(select(ApplicationClient))).scalars().all() == []
    assert await _event_types(session) == []


@pytest.mark.db
async def test_a_refused_rerun_leaves_the_client_as_it_was(session: AsyncSession):
    await _bootstrap_operator()
    before = await _grants(session)

    with pytest.raises(SystemExit) as exit_code:
        await bootstrap.bootstrap_client(
            "operator", application=frozenset({"bot:write"}), delegated=frozenset({"auth:users:login"})
        )

    assert exit_code.value.code == "invalid_scope: Client-only scopes cannot be granted in delegated mode"
    assert await _grants(session) == before


@pytest.mark.db
async def test_bootstrap_client_refuses_an_unknown_scope(session: AsyncSession):
    with pytest.raises(SystemExit) as exit_code:
        await bootstrap.bootstrap_client("portal", application=frozenset({"nope:scope"}), delegated=frozenset())

    assert exit_code.value.code == "invalid_scope: Requested scopes are not known or active"
    assert (await session.execute(select(ApplicationClient))).scalars().all() == []


async def _bootstrap_operator() -> None:
    await bootstrap.bootstrap_client("operator", application=OPERATOR_APPLICATION, delegated=OPERATOR_DELEGATED)


async def _token_scope(session: AsyncSession, client_id: str, secret_line: str) -> str:
    """The scope of a `client_credentials` token obtained with the secret a command printed."""
    match = NEW_SECRET.fullmatch(secret_line)
    assert match
    settings = AuthSettings(secret_key=SecretStr("test-signing-secret-with-at-least-32-bytes"))
    token = await issue_client_token(session, settings, client_id=client_id, client_secret=match["plaintext"])
    return token.scope


async def _grants(session: AsyncSession) -> set[tuple[str, GrantMode]]:
    rows = await session.execute(select(ApplicationClientScopeGrant.scope_key, ApplicationClientScopeGrant.mode))
    return set(rows.tuples())


async def _event_types(session: AsyncSession) -> list[str]:
    return list((await session.execute(select(AuthAuditLog.event_type))).scalars().all())
