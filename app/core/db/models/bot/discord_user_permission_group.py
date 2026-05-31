from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import CreatedAtMixin
from .base import BotBase


class DiscordUserPermissionGroup(CreatedAtMixin, BotBase):
    __tablename__ = "discord_user_permission_group"

    discord_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("bot.discord_user.discord_id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
    )
    group_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey("bot.permission_group.key", ondelete="CASCADE"),
        primary_key=True,
    )
