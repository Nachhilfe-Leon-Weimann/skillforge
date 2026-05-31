from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum, Index, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class MemberRole(StrEnum):
    ADMIN = "admin"
    TUTOR = "tutor"
    STUDENT = "student"


class DiscordUser(TimestampMixin, BotBase):
    __tablename__ = "discord_user"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    role: Mapped[MemberRole] = mapped_column(
        Enum(
            MemberRole,
            name="member_role",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    nick_name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    __table_args__ = BotBase.extend_table_args(
        CheckConstraint("nick_name <> ''", name="ck_discord_user_nick_name_not_empty"),
        Index("ix_discord_user_role_active", "role", "active"),
    )
