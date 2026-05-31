from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class DiscordChannelType(StrEnum):
    CATEGORY = "category"
    TEXT = "text"
    VOICE = "voice"
    THREAD = "thread"
    FORUM = "forum"


class DiscordChannel(TimestampMixin, BotBase):
    __tablename__ = "discord_channel"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    guild_id: Mapped[int] = mapped_column(ForeignKey("bot.discord_guild.guild_id", ondelete="CASCADE"), nullable=False)
    parent_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    type: Mapped[DiscordChannelType] = mapped_column(
        Enum(
            DiscordChannelType,
            name="discord_channel_type",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    managed_by_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = BotBase.extend_table_args(
        UniqueConstraint("guild_id", "channel_id", name="uq_discord_channel_guild_id_channel_id"),
        ForeignKeyConstraint(
            ("guild_id", "parent_channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_discord_channel_parent",
            ondelete="SET NULL (parent_channel_id)",
        ),
        Index("ix_discord_channel_guild_id_deleted_at", "guild_id", "deleted_at"),
        Index("ix_discord_channel_parent_channel_id", "parent_channel_id"),
    )
