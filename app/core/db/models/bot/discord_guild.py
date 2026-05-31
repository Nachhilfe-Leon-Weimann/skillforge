from sqlalchemy import BigInteger, Boolean, Index, Text, false, true
from sqlalchemy.orm import Mapped, mapped_column

from ..shared import TimestampMixin
from .base import BotBase


class DiscordGuild(TimestampMixin, BotBase):
    __tablename__ = "discord_guild"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())

    __table_args__ = BotBase.extend_table_args(
        Index(
            "uq_discord_guild_primary_active",
            "is_primary",
            unique=True,
            postgresql_where=is_primary.is_(true()) & active.is_(true()),
        ),
    )
