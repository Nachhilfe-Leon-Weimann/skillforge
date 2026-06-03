from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.models import CommandEnvChannel, CommandEnvKind, DiscordChannel, DiscordUser

from .errors import CommandEnvConflictError, CommandEnvNotFoundError, CommandEnvValidationError


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


async def upsert_command_env(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    kind: CommandEnvKind,
    owner_discord_id: int | None = None,
) -> CommandEnvChannel:
    """Create or update the command env channel for (guild, channel, kind)."""
    channel = await session.get(DiscordChannel, channel_id)
    if channel is None or channel.guild_id != guild_id:
        raise CommandEnvValidationError("Channel not found in guild")
    if owner_discord_id is not None and await session.get(DiscordUser, owner_discord_id) is None:
        raise CommandEnvValidationError("Owner discord user not found")

    command_env = await session.get(CommandEnvChannel, {"guild_id": guild_id, "channel_id": channel_id, "kind": kind})
    if command_env is None:
        command_env = CommandEnvChannel(
            guild_id=guild_id, channel_id=channel_id, kind=kind, owner_discord_id=owner_discord_id
        )
        session.add(command_env)
    else:
        command_env.owner_discord_id = owner_discord_id

    try:
        await session.flush()
    except IntegrityError as exc:
        raise CommandEnvConflictError("Owner already owns a command env of this kind in the guild") from exc

    return command_env


async def delete_command_env(
    session: AsyncSession,
    *,
    guild_id: int,
    channel_id: int,
    kind: CommandEnvKind,
) -> None:
    command_env = await session.get(CommandEnvChannel, {"guild_id": guild_id, "channel_id": channel_id, "kind": kind})
    if command_env is None:
        raise CommandEnvNotFoundError("Command env channel not found")

    await session.delete(command_env)
    await session.flush()
