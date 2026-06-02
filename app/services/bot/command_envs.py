from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import CommandEnvChannel, CommandEnvKind

from .errors import CommandEnvNotFoundError


async def resolve_command_env(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    kind: CommandEnvKind,
    owner_discord_id: int | None = None,
) -> CommandEnvChannel:
    statement = select(CommandEnvChannel).where(
        CommandEnvChannel.guild_id == guild_id,
        CommandEnvChannel.channel_id == channel_id,
        CommandEnvChannel.kind == kind,
    )
    if owner_discord_id is not None:
        statement = statement.where(CommandEnvChannel.owner_discord_id == owner_discord_id)

    result = await session.execute(statement)
    command_env = result.scalar_one_or_none()
    if command_env is None:
        raise CommandEnvNotFoundError("Command env channel not found")

    return command_env
