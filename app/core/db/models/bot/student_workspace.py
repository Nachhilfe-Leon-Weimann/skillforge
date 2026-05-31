from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class StudentChannelState(StrEnum):
    TUTOR_CATEGORY = "tutor_category"
    ARCHIVE_CATEGORY = "archive_category"


class StudentWorkspace(TimestampMixin, BotBase):
    __tablename__ = "student_workspace"

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_guild.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    student_discord_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_user.discord_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    tutor_discord_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_user.discord_id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_state: Mapped[StudentChannelState] = mapped_column(
        Enum(
            StudentChannelState,
            name="student_channel_state",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=StudentChannelState.TUTOR_CATEGORY,
        server_default=text("'tutor_category'"),
    )
    current_parent_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    archive_category_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    stashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    popped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = BotBase.extend_table_args(
        UniqueConstraint("channel_id", name="uq_student_workspace_channel_id"),
        ForeignKeyConstraint(
            ("guild_id", "channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_student_workspace_channel",
            ondelete="SET NULL (channel_id)",
        ),
        ForeignKeyConstraint(
            ("guild_id", "current_parent_channel_id"),
            ("bot.discord_channel.guild_id", "bot.discord_channel.channel_id"),
            name="fk_student_workspace_current_parent_channel",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("guild_id", "archive_category_channel_id"),
            ("bot.archive_category.guild_id", "bot.archive_category.category_channel_id"),
            name="fk_student_workspace_archive_category",
            ondelete="RESTRICT",
        ),
        Index("ix_student_workspace_guild_id_tutor_discord_id", "guild_id", "tutor_discord_id"),
        Index(
            "ix_student_workspace_guild_id_tutor_discord_id_channel_state",
            "guild_id",
            "tutor_discord_id",
            "channel_state",
        ),
        Index("ix_student_workspace_guild_id_channel_state", "guild_id", "channel_state"),
        Index(
            "ix_student_workspace_archive_category_channel_id_channel_state",
            "archive_category_channel_id",
            "channel_state",
        ),
        CheckConstraint(
            "(channel_state = 'tutor_category' AND archive_category_channel_id IS NULL) "
            "OR (channel_state = 'archive_category' AND archive_category_channel_id IS NOT NULL)",
            name="ck_student_workspace_archive_category_matches_state",
        ),
        CheckConstraint(
            "channel_state <> 'archive_category' OR channel_id IS NOT NULL",
            name="ck_student_workspace_archive_requires_channel",
        ),
        CheckConstraint(
            "channel_state <> 'archive_category' OR current_parent_channel_id = archive_category_channel_id",
            name="ck_student_workspace_archive_parent",
        ),
        CheckConstraint(
            "channel_state <> 'tutor_category' OR channel_id IS NULL OR current_parent_channel_id IS NOT NULL",
            name="ck_student_workspace_tutor_category_parent",
        ),
    )
