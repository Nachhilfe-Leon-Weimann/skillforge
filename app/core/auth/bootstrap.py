"""Operator commands that seed the auth state: `just bootstrap-skillbot`, `just bootstrap-admin`.

Both print their one-time secret to stdout and nowhere else: the operator reads it from the
terminal and passes it on. It must never go through the logger.
"""

import argparse
import asyncio
import uuid

from app.core.auth.config import AuthSettings
from app.core.auth.scopes import Scope
from app.core.auth.services import bootstrap_application_client
from app.core.auth.services.bootstrap import bootstrap_admin_account
from app.core.config import get_settings
from app.core.db import Database


async def bootstrap_skillbot() -> None:
    settings = get_settings()
    db = Database.from_url(str(settings.db.url))

    try:
        async with db.session() as session:
            result = await bootstrap_application_client(
                session,
                client_id="skillbot",
                name="SkillBot",
                description="Discord Bot",
                scopes=(Scope.BOT_READ, Scope.BOT_WRITE),
            )
    finally:
        await db.dispose()

    print(f"client_id={result.client.client_id}")
    print(f"scopes={' '.join(sorted(result.granted_scopes))}")
    if result.created_secret is None:
        print("client_secret=<existing usable secret retained>")
    else:
        print(f"client_secret={result.created_secret.plaintext}")


async def bootstrap_admin(*, party_id: uuid.UUID, email: str) -> None:
    """Give a person party a user account with the ``admin`` role and print its invitation.

    The first admin cannot be invited - inviting needs one (decision O). Run again it keeps the
    account and issues a fresh invitation only while the account has no password.
    """
    settings = get_settings()
    db = Database.from_url(str(settings.db.url))
    auth_settings: AuthSettings = settings.auth

    try:
        async with db.session() as session:
            result = await bootstrap_admin_account(session, auth_settings, party_id=party_id, email=email)
    finally:
        await db.dispose()

    print(f"user_id={result.account.id}")
    print(f"email={result.account.email}")
    print(f"status={result.account.status.value}")
    if result.invitation is None:
        print("invitation=<account already has a password; reset it through POST /auth/users/{user_id}/password-reset>")
    else:
        print(f"invitation_token={result.invitation.plaintext}")
        print(f"invitation_expires_at={result.invitation.token.expires_at.isoformat()}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.core.auth.bootstrap", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("skillbot", help="Seed the SkillBot application client and print its secret.")
    admin = commands.add_parser("admin", help="Seed an admin user account and print its invitation token.")
    admin.add_argument("--party-id", type=uuid.UUID, required=True, help="ID of the person party to bind it to.")
    admin.add_argument("--email", required=True, help="The login e-mail address of the account.")

    arguments = parser.parse_args()
    if arguments.command == "skillbot":
        asyncio.run(bootstrap_skillbot())
    else:
        asyncio.run(bootstrap_admin(party_id=arguments.party_id, email=arguments.email))


if __name__ == "__main__":
    main()
