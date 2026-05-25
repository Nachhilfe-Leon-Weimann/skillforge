import asyncio

from app.core.auth.scopes import Scope
from app.core.auth.services import bootstrap_application_client
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


def main() -> None:
    asyncio.run(bootstrap_skillbot())


if __name__ == "__main__":
    main()
