from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class ArchiveCategory(TimestampMixin, BotBase):
    __tablename__ = "archive_category"

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_guild.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    archive_no: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    category_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capacity: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=50,
        server_default=text("50"),
    )

    __table_args__ = BotBase.extend_table_args(
        ForeignKeyConstraint(
            ("guild_id", "category_channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_archive_category_channel",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("category_channel_id", name="uq_archive_category_category_channel_id"),
        UniqueConstraint("guild_id", "category_channel_id", name="uq_archive_category_guild_id_category_channel_id"),
        Index("ix_archive_category_guild_id", "guild_id"),
        CheckConstraint("archive_no > 0", name="ck_archive_category_archive_no_positive"),
        CheckConstraint("capacity BETWEEN 1 AND 50", name="ck_archive_category_capacity"),
    )
