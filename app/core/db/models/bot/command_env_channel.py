from enum import StrEnum

from sqlalchemy import BigInteger, Enum, ForeignKey, ForeignKeyConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class CommandEnvKind(StrEnum):
    ADMIN_CMD = "admin_cmd"
    TUTOR_CMD = "tutor_cmd"


class CommandEnvChannel(TimestampMixin, BotBase):
    __tablename__ = "command_env_channel"

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_guild.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    kind: Mapped[CommandEnvKind] = mapped_column(
        Enum(
            CommandEnvKind,
            name="command_env_kind",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        primary_key=True,
    )
    owner_discord_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_user.discord_id", ondelete="CASCADE"),
        nullable=True,
    )

    __table_args__ = BotBase.extend_table_args(
        ForeignKeyConstraint(
            ("guild_id", "channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_command_env_channel_channel",
            ondelete="CASCADE",
        ),
        Index("ix_command_env_channel_guild_id_kind", "guild_id", "kind"),
        Index("ix_command_env_channel_guild_id_owner_discord_id_kind", "guild_id", "owner_discord_id", "kind"),
        Index(
            "uq_command_env_channel_guild_id_owner_discord_id_kind",
            "guild_id",
            "owner_discord_id",
            "kind",
            unique=True,
            postgresql_where=owner_discord_id.is_not(None),
        ),
    )
