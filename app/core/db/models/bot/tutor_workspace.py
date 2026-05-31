from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class TutorWorkspace(TimestampMixin, BotBase):
    __tablename__ = "tutor_workspace"

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_guild.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    tutor_discord_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_user.discord_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    category_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    student_channel_capacity: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=49,
        server_default=text("49"),
    )

    __table_args__ = BotBase.extend_table_args(
        ForeignKeyConstraint(
            ("guild_id", "category_channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_tutor_workspace_category_channel",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("guild_id", "command_channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_tutor_workspace_command_channel",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("category_channel_id", name="uq_tutor_workspace_category_channel_id"),
        UniqueConstraint("command_channel_id", name="uq_tutor_workspace_command_channel_id"),
        CheckConstraint(
            "student_channel_capacity BETWEEN 0 AND 49",
            name="ck_tutor_workspace_student_channel_capacity",
        ),
    )
