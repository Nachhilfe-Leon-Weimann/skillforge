"""Operator commands that seed application clients: `just bootstrap-skillbot`, `just bootstrap-client`.

Both print a client secret they created to stdout and nowhere else: the operator reads it from the
terminal and passes it on. It must never go through the logger.
"""

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.results import CreatedClientSecret
from app.core.auth.scopes import Scope, format_scopes, parse_scopes
from app.core.auth.services import InvalidClientScopeError, bootstrap_application_client
from app.core.config import get_settings
from app.core.db import Database
from app.core.db.models import GrantMode


async def bootstrap_skillbot() -> None:
    async with _session() as session:
        result = await bootstrap_application_client(
            session,
            client_id="skillbot",
            name="SkillBot",
            description="Discord Bot",
            scopes=(Scope.BOT_READ, Scope.BOT_WRITE),
        )

    print(f"client_id={result.client.client_id}")
    print(f"scopes={format_scopes(result.granted_scopes)}")
    _print_secret(result.created_secret)


async def bootstrap_client(client_id: str, *, application: frozenset[str], delegated: frozenset[str]) -> None:
    """Ensure an active client, its grants in both modes and a usable secret.

    A new client is named after its ID. Run again, or on a client created through the API, it keeps
    the client's name and description, its secret and every grant it holds, and re-enables a
    disabled client. An unknown scope, or a client-only one in ``delegated``, is ``invalid_scope``
    and changes nothing.
    """
    grants = {GrantMode.APPLICATION: application, GrantMode.DELEGATED: delegated}
    try:
        async with _session() as session:
            results = {
                mode: await bootstrap_application_client(session, client_id=client_id, scopes=scopes, mode=mode)
                for mode, scopes in grants.items()
            }
    except InvalidClientScopeError as exc:
        raise SystemExit(f"invalid_scope: {exc}") from None

    print(f"client_id={client_id}")
    for mode, result in results.items():
        print(f"{mode}_scopes={format_scopes(result.granted_scopes)}")
    # The first call creates a missing secret, so the second finds a usable one.
    _print_secret(results[GrantMode.APPLICATION].created_secret)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.core.auth.bootstrap", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("skillbot", help="Seed the SkillBot application client and print its secret.")
    client = commands.add_parser("client", help="Seed an application client with grants in both modes.")
    client.add_argument("client_id", type=_client_id, help="Client ID of the application client.")
    client.add_argument(
        "--application",
        type=parse_scopes,
        required=True,
        help="Space-separated scopes the client may use for itself.",
    )
    client.add_argument(
        "--delegated",
        type=parse_scopes,
        required=True,
        help="Space-separated scopes the client may use at most for a person (the ceiling).",
    )
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.command == "skillbot":
        asyncio.run(bootstrap_skillbot())
    else:
        asyncio.run(
            bootstrap_client(arguments.client_id, application=arguments.application, delegated=arguments.delegated)
        )


def _client_id(value: str) -> str:
    """The client ID as given on the command line, stripped; an empty one is refused."""
    if not (client_id := value.strip()):
        raise argparse.ArgumentTypeError("must not be empty")

    return client_id


@asynccontextmanager
async def _session() -> AsyncIterator[AsyncSession]:
    """A session on the configured database whose transaction commits when the block succeeds."""
    db = Database.from_url(str(get_settings().db.url))
    try:
        async with db.session() as session:
            yield session
    finally:
        await db.dispose()


def _print_secret(created_secret: CreatedClientSecret | None) -> None:
    if created_secret is None:
        print("client_secret=<existing usable secret retained>")
    else:
        print(f"client_secret={created_secret.plaintext}")


if __name__ == "__main__":
    main()
