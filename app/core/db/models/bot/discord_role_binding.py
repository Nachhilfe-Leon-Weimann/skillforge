from sqlalchemy import BigInteger, Boolean, Enum, ForeignKey, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase
from .discord_user import MemberRole


class DiscordRoleBinding(TimestampMixin, BotBase):
    __tablename__ = "discord_role_binding"

    guild_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_guild.guild_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    member_role: Mapped[MemberRole] = mapped_column(
        Enum(
            MemberRole,
            name="member_role",
            schema="bot",
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        primary_key=True,
    )
    role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    role_name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
